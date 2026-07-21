import discord
from discord.ext import commands
import mido
import os
import statistics
import tempfile

# === 音楽処理用パラメーター ===
ALPHA = 0.3
PENALTY_CROSS_HAND = 50
PENALTY_DURATION = 8
PENALTY_OCTAVE = 80
PENALTY_EXTREME_RANGE = 35
EXTREME_LOW = 45
EXTREME_HIGH = 84
PENALTY_IDLE_IMBALANCE = 3.0
IDLE_THRESHOLD_BEATS = 1.0
JUMP_THRESHOLD = 7
MAX_REPAIR_PASSES = 4
PEDAL_DURATION_BEATS = 4

# === お掃除（MuseScore最適化）用パラメーター ===
QUANTIZE_TOLERANCE = 30
CUT_OVERLAP = True

# === 自動パラメータ推定用パラメーター ===
PIANO_NOTE_MIN = 21
PIANO_NOTE_MAX = 108
EXTREME_RANGE_MIN_NOTES = 8

QUANTIZE_GRID_DIVISORS = [32, 24, 16, 12, 8, 6, 4, 3, 2, 1]
QUANTIZE_MIN_NOTES = 12
QUANTIZE_MATCH_RATIO = 0.9
QUANTIZE_GRID_ERROR_RATIO = 0.15
QUANTIZE_TOLERANCE_MIN = 5
QUANTIZE_TOLERANCE_MAX = 60


def cut_overlap_and_build(events, ticks_per_beat):
    if not events: return []
    active_notes = {}
    note_list = []
    other_events = []

    for ev in sorted(events, key=lambda x: x['time']):
        msg = ev['msg']
        t = ev['time']
        if msg.type == 'note_on' and msg.velocity > 0:
            if msg.note in active_notes:
                active_notes[msg.note]['end'] = t
                note_list.append(active_notes[msg.note])
            active_notes[msg.note] = {
                'note': msg.note, 'velocity': msg.velocity,
                'start': t, 'end': None, 'channel': msg.channel
            }
        elif msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0):
            if msg.note in active_notes:
                active_notes[msg.note]['end'] = t
                note_list.append(active_notes[msg.note])
                del active_notes[msg.note]
        else:
            other_events.append({'msg': msg.copy(), 'time': t})

    for n in active_notes.values():
        n['end'] = n['start'] + ticks_per_beat
        note_list.append(n)

    note_list.sort(key=lambda x: x['start'])

    if CUT_OVERLAP:
        start_times = sorted(list(set([n['start'] for n in note_list])))
        for n in note_list:
            next_starts = [t for t in start_times if t > n['start']]
            if next_starts:
                next_start_time = next_starts[0]
                if n['end'] > next_start_time:
                    n['end'] = next_start_time

    final_events = []
    for n in note_list:
        final_events.append({
            'msg': mido.Message('note_on', channel=n['channel'], note=n['note'], velocity=n['velocity'], time=0),
            'time': n['start'], 'type': 'on'
        })
        final_events.append({
            'msg': mido.Message('note_off', channel=n['channel'], note=n['note'], velocity=0, time=0),
            'time': n['end'], 'type': 'off'
        })

    for ev in other_events:
        final_events.append({'msg': ev['msg'], 'time': ev['time'], 'type': 'other'})

    def sort_key(x):
        priority = 1 if x['type'] == 'off' else 2
        return (x['time'], priority)

    final_events.sort(key=sort_key)
    return final_events

def quantize_note_starts(note_objects, tolerance):
    note_objects.sort(key=lambda x: x['start'])
    last_time = -1
    for n in note_objects:
        if last_time != -1 and abs(n['start'] - last_time) <= tolerance:
            shift = n['start'] - last_time
            n['start'] = last_time
            n['end'] -= shift
        else:
            last_time = n['start']
    return note_objects

def cluster_note_objects(note_objects, tolerance):
    clusters = []
    cur_cluster = []
    cur_time = None

    for n in note_objects:
        if cur_time is None or n['start'] - cur_time <= tolerance:
            cur_cluster.append(n)
            cur_time = n['start'] if cur_time is None else cur_time
        else:
            clusters.append(cur_cluster)
            cur_cluster = [n]
            cur_time = n['start']

    if cur_cluster:
        clusters.append(cur_cluster)

    return clusters

def _remove_by_identity(lst, obj):
    for i, x in enumerate(lst):
        if x is obj:
            del lst[i]
            return

def _is_pedal_note(n, ticks_per_beat):
    return (n['end'] - n['start']) / ticks_per_beat >= PEDAL_DURATION_BEATS

def _try_group_swap(hand, note, other_active, remaining_in_hand, max_span):
    if hand == 'right':
        candidates = sorted(other_active, key=lambda o: -o['note'])
    else:
        candidates = sorted(other_active, key=lambda o: o['note'])

    kept = list(other_active)
    evicted = []
    idx = 0
    while kept:
        vals = [o['note'] for o in kept] + [note]
        if max(vals) - min(vals) <= max_span:
            break
        if idx >= len(candidates):
            return None
        worst = candidates[idx]
        idx += 1
        if worst not in kept:
            continue
        kept = [o for o in kept if o is not worst]
        evicted.append(worst)

    vals = [o['note'] for o in kept] + [note]
    if kept and (max(vals) - min(vals)) > max_span:
        return None
    if not evicted:
        return None

    combo = [o['note'] for o in remaining_in_hand] + [o['note'] for o in evicted]
    if combo and (max(combo) - min(combo)) > max_span:
        return None

    final_other_pitches = [o['note'] for o in kept] + [note]
    final_hand_pitches = combo
    if final_hand_pitches and final_other_pitches:
        if hand == 'right':
            if min(final_hand_pitches) < max(final_other_pitches):
                return None
        else:
            if max(final_hand_pitches) > min(final_other_pitches):
                return None

    return evicted

def repair_hand_crossing(assigned_r, assigned_l, ticks_per_beat, max_span):
    def active_notes_in(hand, start_t, end_t, exclude_obj=None):
        src = assigned_r if hand == 'right' else assigned_l
        return [an for an in src
                if an is not exclude_obj and not _is_pedal_note(an, ticks_per_beat)
                and not (an['end'] <= start_t or an['start'] >= end_t)]

    def move_note(note_obj, from_hand, to_hand):
        if from_hand == 'right':
            _remove_by_identity(assigned_r, note_obj)
            assigned_l.append(note_obj)
        else:
            _remove_by_identity(assigned_l, note_obj)
            assigned_r.append(note_obj)

    changed_any = False
    for _pass in range(MAX_REPAIR_PASSES):
        timeline = sorted(
            [{'n': n, 'hand': 'right'} for n in assigned_r] +
            [{'n': n, 'hand': 'left'} for n in assigned_l],
            key=lambda x: x['n']['start']
        )
        changed = False

        for item in timeline:
            n = item['n']
            if _is_pedal_note(n, ticks_per_beat):
                continue
            hand = item['hand']
            other = 'left' if hand == 'right' else 'right'
            note = n['note']
            start_t, end_t = n['start'], n['end']

            other_active = active_notes_in(other, start_t, end_t)
            if hand == 'right':
                crosses_now = any(o['note'] > note for o in other_active)
            else:
                crosses_now = any(o['note'] < note for o in other_active)

            if crosses_now:
                dest_pitches = [o['note'] for o in other_active] + [note]
                span_after = max(dest_pitches) - min(dest_pitches)

                remaining_in_hand = active_notes_in(hand, start_t, end_t, exclude_obj=n)
                if hand == 'right':
                    still_crosses = bool(remaining_in_hand) and min(an['note'] for an in remaining_in_hand) < note
                else:
                    still_crosses = bool(remaining_in_hand) and max(an['note'] for an in remaining_in_hand) > note

                if span_after <= max_span and not still_crosses:
                    move_note(n, hand, other)
                    item['hand'] = other
                    changed = True
                else:
                    evicted = _try_group_swap(hand, note, other_active, remaining_in_hand, max_span)
                    if evicted is not None:
                        move_note(n, hand, other)
                        item['hand'] = other
                        for ev in evicted:
                            move_note(ev, other, hand)
                        changed = True

        if changed:
            changed_any = True
        if not changed:
            break
    return changed_any

def repair_octave_span(assigned_r, assigned_l, ticks_per_beat, max_span):
    def active_notes_in(hand, start_t, end_t, exclude_obj=None):
        src = assigned_r if hand == 'right' else assigned_l
        return [an for an in src
                if an is not exclude_obj and not _is_pedal_note(an, ticks_per_beat)
                and not (an['end'] <= start_t or an['start'] >= end_t)]

    def move_note(note_obj, from_hand, to_hand):
        if from_hand == 'right':
            _remove_by_identity(assigned_r, note_obj)
            assigned_l.append(note_obj)
        else:
            _remove_by_identity(assigned_l, note_obj)
            assigned_r.append(note_obj)

    changed_any = False
    for _pass in range(MAX_REPAIR_PASSES):
        timeline = sorted(
            [{'n': n, 'hand': 'right'} for n in assigned_r] +
            [{'n': n, 'hand': 'left'} for n in assigned_l],
            key=lambda x: x['n']['start']
        )
        changed = False

        for item in timeline:
            n = item['n']
            if _is_pedal_note(n, ticks_per_beat):
                continue
            hand = item['hand']
            other = 'left' if hand == 'right' else 'right'
            note = n['note']
            start_t, end_t = n['start'], n['end']

            same_hand_active = active_notes_in(hand, start_t, end_t)
            if len(same_hand_active) < 2:
                continue
            pitches = [o['note'] for o in same_hand_active]
            if (max(pitches) - min(pitches)) <= max_span:
                continue

            center = sum(pitches) / len(pitches)
            worst = max(same_hand_active, key=lambda o: abs(o['note'] - center))
            if worst is not n:
                continue

            other_active = active_notes_in(other, start_t, end_t)
            dest_pitches = [o['note'] for o in other_active] + [note]
            span_after = max(dest_pitches) - min(dest_pitches)

            remaining_in_hand = active_notes_in(hand, start_t, end_t, exclude_obj=n)
            if hand == 'right':
                crosses = bool(other_active) and max(o['note'] for o in other_active) > note
            else:
                crosses = bool(other_active) and min(o['note'] for o in other_active) < note

            if span_after <= max_span and not crosses:
                move_note(n, hand, other)
                item['hand'] = other
                changed = True
            else:
                evicted = _try_group_swap(hand, note, other_active, remaining_in_hand, max_span)
                if evicted is not None:
                    move_note(n, hand, other)
                    item['hand'] = other
                    for ev in evicted:
                        move_note(ev, other, hand)
                    changed = True

        if changed:
            changed_any = True
        if not changed:
            break
    return changed_any

def repair_idle_imbalance(assigned_r, assigned_l, ticks_per_beat, max_span):
    def active_notes_in(hand, start_t, end_t, exclude_obj=None):
        src = assigned_r if hand == 'right' else assigned_l
        return [an['note'] for an in src
                if an is not exclude_obj and not (an['end'] <= start_t or an['start'] >= end_t)]

    for _pass in range(MAX_REPAIR_PASSES):
        timeline = sorted(
            [{'n': n, 'hand': 'right'} for n in assigned_r] +
            [{'n': n, 'hand': 'left'} for n in assigned_l],
            key=lambda x: x['n']['start']
        )
        last_end = {'right': None, 'left': None}
        ema = {'right': 72.0, 'left': 48.0}
        changed = False

        for item in timeline:
            n = item['n']
            hand = item['hand']
            start_t, end_t, note = n['start'], n['end'], n['note']

            idle_right = (start_t - last_end['right']) / ticks_per_beat if last_end['right'] is not None else float('inf')
            jump_here = abs(note - ema[hand])

            if hand == 'left' and idle_right > IDLE_THRESHOLD_BEATS and jump_here > JUMP_THRESHOLD:
                other_active = active_notes_in('right', start_t, end_t)
                span_after = (max(other_active + [note]) - min(other_active + [note])) if other_active else 0
                remaining_in_hand = active_notes_in('left', start_t, end_t, exclude_obj=n)
                crosses = bool(remaining_in_hand) and max(remaining_in_hand) > note

                if span_after <= max_span and not crosses:
                    _remove_by_identity(assigned_l, n)
                    assigned_r.append(n)
                    item['hand'] = 'right'
                    changed = True

            final_hand = item['hand']
            last_end[final_hand] = end_t
            ema[final_hand] = (ALPHA * note) + ((1 - ALPHA) * ema[final_hand])

        if not changed:
            break

def estimate_extreme_range(note_objects):
    pitches = [n['note'] for n in note_objects]
    if len(pitches) < EXTREME_RANGE_MIN_NOTES:
        return EXTREME_LOW, EXTREME_HIGH

    q1, _, q3 = statistics.quantiles(pitches, n=4, method='inclusive')
    iqr = q3 - q1
    if iqr <= 0:
        return EXTREME_LOW, EXTREME_HIGH

    low = max(PIANO_NOTE_MIN, min(PIANO_NOTE_MAX, round(q1 - 1.5 * iqr)))
    high = max(PIANO_NOTE_MIN, min(PIANO_NOTE_MAX, round(q3 + 1.5 * iqr)))

    if low >= high:
        return EXTREME_LOW, EXTREME_HIGH

    return int(low), int(high)

def estimate_quantize_grid(note_objects, ticks_per_beat):
    starts = [n['start'] for n in note_objects]
    if len(starts) < QUANTIZE_MIN_NOTES:
        return None

    for d in QUANTIZE_GRID_DIVISORS:
        grid = ticks_per_beat / d
        if grid < 1:
            continue

        err_limit = grid * QUANTIZE_GRID_ERROR_RATIO
        matched = 0
        for t in starts:
            remainder = t % grid
            dist = min(remainder, grid - remainder)
            if dist <= err_limit:
                matched += 1

        if matched / len(starts) >= QUANTIZE_MATCH_RATIO:
            return grid

    return None

def estimate_quantize_tolerance(grid, ticks_per_beat):
    if grid is None:
        return QUANTIZE_TOLERANCE

    tol_max = min(QUANTIZE_TOLERANCE_MAX, max(QUANTIZE_TOLERANCE_MIN, ticks_per_beat // 2))
    tol = max(QUANTIZE_TOLERANCE_MIN, min(tol_max, grid / 2))
    return int(round(tol))

def quantize_notes_to_grid(note_objects, grid, ticks_per_beat):
    if not grid:
        return note_objects

    for n in note_objects:
        duration = n['end'] - n['start']
        new_start = int(round(n['start'] / grid) * grid)
        quantized_duration = max(grid, round(duration / grid) * grid)
        n['start'] = new_start
        n['end'] = new_start + int(round(quantized_duration))
        n['duration_beats'] = (n['end'] - n['start']) / ticks_per_beat

    note_objects.sort(key=lambda x: x['start'])
    return note_objects

def split_single_track(input_track, ticks_per_beat, ticks_per_bar):
    abs_time = 0
    active_notes = {}
    note_objects = []
    other_events = []

    for msg in input_track:
        abs_time += msg.time
        if msg.type == 'end_of_track': continue

        if msg.type == 'note_on' and msg.velocity > 0:
            active_notes[msg.note] = {'start': abs_time, 'vel': msg.velocity, 'msg': msg}
        elif msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0):
            if msg.note in active_notes:
                on_data = active_notes[msg.note]
                note_objects.append({
                    'note': msg.note,
                    'start': on_data['start'],
                    'end': abs_time,
                    'duration_beats': (abs_time - on_data['start']) / ticks_per_beat,
                    'vel': on_data['vel'],
                    'msg': on_data['msg']
                })
                del active_notes[msg.note]
        else:
            other_events.append({'msg': msg.copy(), 'time': abs_time})

    for note_num, on_data in active_notes.items():
        note_objects.append({
            'note': note_num, 'start': on_data['start'], 'end': on_data['start'] + ticks_per_beat,
            'duration_beats': 1.0, 'vel': on_data['vel'], 'msg': on_data['msg']
        })

    note_objects.sort(key=lambda x: x['start'])

    extreme_low, extreme_high = estimate_extreme_range(note_objects)
    quantize_grid = estimate_quantize_grid(note_objects, ticks_per_beat)
    quantize_tolerance = estimate_quantize_tolerance(quantize_grid, ticks_per_beat)

    if quantize_grid:
        note_objects = quantize_notes_to_grid(note_objects, quantize_grid, ticks_per_beat)
    else:
        note_objects = quantize_note_starts(note_objects, quantize_tolerance)

    bar_averages = {}
    for n in note_objects:
        b_idx = n['start'] // ticks_per_bar
        if b_idx not in bar_averages:
            bar_averages[b_idx] = []
        bar_averages[b_idx].append(n['note'])

    for b_idx in bar_averages:
        notes = bar_averages[b_idx]
        bar_averages[b_idx] = sum(notes) / len(notes)

    ema_right = 72.0
    ema_left = 48.0
    last_dur_r = 1.0
    last_dur_l = 1.0
    last_end_r = 0

    current_bar = -1
    assigned_r = []
    assigned_l = []

    clusters = cluster_note_objects(note_objects, quantize_tolerance)
    MAX_SPAN = 12

    for cluster in clusters:
        cluster.sort(key=lambda x: x['note'])
        start_t = cluster[0]['start']

        note_bar = start_t // ticks_per_bar
        if note_bar > current_bar:
            current_bar = note_bar
            bar_avg = bar_averages.get(current_bar, 60.0)
            ema_right = (ema_right + (bar_avg + 6)) / 2
            ema_left = (ema_left + (bar_avg - 6)) / 2

        prelim = {}
        for n in cluster:
            note = n['note']
            dur = n['duration_beats']
            n_start, n_end = n['start'], n['end']

            cost_r = abs(note - ema_right) + abs(dur - last_dur_r) * PENALTY_DURATION
            cost_l = abs(note - ema_left) + abs(dur - last_dur_l) * PENALTY_DURATION

            active_r = [an['note'] for an in assigned_r if not _is_pedal_note(an, ticks_per_beat) and not (an['end'] <= n_start or an['start'] >= n_end)]
            active_l = [an['note'] for an in assigned_l if not _is_pedal_note(an, ticks_per_beat) and not (an['end'] <= n_start or an['start'] >= n_end)]

            active_r += [c['note'] for c in cluster if c is not n and prelim.get(id(c)) == 'right']
            active_l += [c['note'] for c in cluster if c is not n and prelim.get(id(c)) == 'left']

            if active_r and (max(active_r + [note]) - min(active_r + [note])) >= 12:
                cost_r += PENALTY_OCTAVE
            if active_l and (max(active_l + [note]) - min(active_l + [note])) >= 12:
                cost_l += PENALTY_OCTAVE

            if note < ema_left: cost_r += PENALTY_CROSS_HAND
            if note > ema_right: cost_l += PENALTY_CROSS_HAND

            if note < extreme_low:
                cost_r += PENALTY_EXTREME_RANGE
            elif note > extreme_high:
                cost_l += PENALTY_EXTREME_RANGE

            idle_r_beats = (n_start - last_end_r) / ticks_per_beat
            move_l = abs(note - ema_left)

            if idle_r_beats > IDLE_THRESHOLD_BEATS:
                cost_l += move_l * PENALTY_IDLE_IMBALANCE

            prelim[id(n)] = 'right' if cost_r <= cost_l else 'left'

        for _ in range(6):
            violated = False
            for hand, other in (('right', 'left'), ('left', 'right')):
                hand_notes = [n for n in cluster if prelim[id(n)] == hand]
                if len(hand_notes) < 2:
                    continue
                pitches = [n['note'] for n in hand_notes]
                if max(pitches) - min(pitches) > MAX_SPAN:
                    center = sum(pitches) / len(pitches)
                    outlier = max(hand_notes, key=lambda n: abs(n['note'] - center))
                    prelim[id(outlier)] = other
                    violated = True
            if not violated:
                break

        for n in cluster:
            note = n['note']
            dur = n['duration_beats']
            if prelim[id(n)] == 'right':
                ema_right = (ALPHA * note) + ((1 - ALPHA) * ema_right)
                last_dur_r = dur
                last_end_r = n['end']
                assigned_r.append(n)
            else:
                ema_left = (ALPHA * note) + ((1 - ALPHA) * ema_left)
                last_dur_l = dur
                assigned_l.append(n)

    for _ in range(MAX_REPAIR_PASSES):
        c1 = repair_hand_crossing(assigned_r, assigned_l, ticks_per_beat, MAX_SPAN)
        c2 = repair_octave_span(assigned_r, assigned_l, ticks_per_beat, MAX_SPAN)
        if not c1 and not c2:
            break
    repair_idle_imbalance(assigned_r, assigned_l, ticks_per_beat, MAX_SPAN)

    events_r = []
    events_l = []

    for n in assigned_r:
        events_r.append({'msg': n['msg'].copy(), 'time': n['start']})
        events_r.append({'msg': mido.Message('note_off', channel=n['msg'].channel, note=n['note'], velocity=0, time=0), 'time': n['end']})

    for n in assigned_l:
        events_l.append({'msg': n['msg'].copy(), 'time': n['start']})
        events_l.append({'msg': mido.Message('note_off', channel=n['msg'].channel, note=n['note'], velocity=0, time=0), 'time': n['end']})

    for ev in other_events:
        events_r.append(ev)
        events_l.append(ev.copy())

    cleaned_r = cut_overlap_and_build(events_r, ticks_per_beat)
    cleaned_l = cut_overlap_and_build(events_l, ticks_per_beat)

    def build_track(events, name_suffix):
        track = mido.MidiTrack()
        track.append(mido.MetaMessage('track_name', name=f"Split_{name_suffix}", time=0))
        events.sort(key=lambda x: x['time'])
        last_time = 0
        for ev in events:
            new_msg = ev['msg']
            new_msg.time = ev['time'] - last_time
            track.append(new_msg)
            last_time = ev['time']
        return track

    split_params = {
        'extreme_low': extreme_low,
        'extreme_high': extreme_high,
        'quantize_tolerance': quantize_tolerance,
    }
    return build_track(cleaned_r, "Right"), build_track(cleaned_l, "Left"), split_params

def copy_track_clean(input_track):
    track = mido.MidiTrack()
    abs_time = 0
    events = []
    for msg in input_track:
        abs_time += msg.time
        if msg.type == 'end_of_track': continue
        events.append({'msg': msg.copy(), 'time': abs_time})
    last_time = 0
    events.sort(key=lambda x: x['time'])
    for ev in events:
        new_msg = ev['msg']
        new_msg.time = ev['time'] - last_time
        track.append(new_msg)
        last_time = ev['time']
    return track

def detect_time_signature(input_midi):
    beats_per_bar = 4
    denominator = 4

    for track in input_midi.tracks:
        for msg in track:
            if msg.type == 'time_signature':
                beats_per_bar = msg.numerator
                denominator = msg.denominator
                return beats_per_bar, denominator
    return beats_per_bar, denominator


class MidiSplit(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    @commands.dm_only()  # DMでのみコマンドを受け付ける制限
    async def split(self, ctx, target_track_index: int):
        """
        使い方: !split <数字>
        必ずMIDIファイルを添付して送信してください。
        """
        if not ctx.message.attachments:
            await ctx.send("【エラー】MIDIファイルを添付して送信してください。")
            return

        attachment = ctx.message.attachments[0]
        if not attachment.filename.lower().endswith(('.mid', '.midi')):
            await ctx.send("【エラー】MIDIファイル（.mid または .midi）を添付してください。")
            return

        await ctx.send(f"トラック {target_track_index} の分割処理を開始します。少々お待ちください...")

        # 安全のため一時ディレクトリに保存して処理
        with tempfile.TemporaryDirectory() as tmpdirname:
            input_filepath = os.path.join(tmpdirname, "input.mid")
            output_filepath = os.path.join(tmpdirname, "output.mid")

            # ファイルのダウンロード
            await attachment.save(input_filepath)

            try:
                # 音楽処理のメインロジック
                input_midi = mido.MidiFile(input_filepath)

                # ターゲットトラックが存在するか確認
                if target_track_index >= len(input_midi.tracks) or target_track_index < 0:
                    await ctx.send(f"【エラー】指定されたトラック番号 ({target_track_index}) が不正です。ファイルには {len(input_midi.tracks)} 個のトラックしかありません。")
                    return

                beats_per_bar, denominator = detect_time_signature(input_midi)
                ticks_per_bar = input_midi.ticks_per_beat * beats_per_bar * (4 / denominator)

                output_midi = mido.MidiFile(type=1)
                output_midi.ticks_per_beat = input_midi.ticks_per_beat

                split_params = None
                for i, track in enumerate(input_midi.tracks):
                    if i == target_track_index:
                        track_r, track_l, split_params = split_single_track(track, input_midi.ticks_per_beat, ticks_per_bar)
                        output_midi.tracks.append(track_r)
                        output_midi.tracks.append(track_l)
                    else:
                        output_midi.tracks.append(copy_track_clean(track))

                output_midi.save(output_filepath)

                # 結果の送信
                result_file = discord.File(output_filepath, filename=f"split_{attachment.filename}")
                param_note = ""
                if split_params:
                    param_note = (
                        f"\n検出パラメータ: 音域境界={split_params['extreme_low']}〜{split_params['extreme_high']}"
                        f" / クオンタイズ許容値={split_params['quantize_tolerance']}tick"
                    )
                await ctx.send(f"処理が完了しました！{param_note}", file=result_file)

            except Exception as e:
                await ctx.send(f"【エラー】処理中に問題が発生しました:\n`{str(e)}`")

    @split.error
    async def split_error(self, ctx, error):
        if isinstance(error, commands.PrivateMessageOnly):
            await ctx.send("このコマンドはDM（ダイレクトメッセージ）でのみ実行可能です。")
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send("【エラー】対象のトラック番号（数字）を指定してください。\n例: `!split 2`")
        elif isinstance(error, commands.BadArgument):
            await ctx.send("【エラー】トラック番号は数字で入力してください。")


async def setup(bot):
    await bot.add_cog(MidiSplit(bot))
