import discord
from discord import app_commands
from discord.ext import commands
import yt_dlp
import asyncio

class MusicCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # --------------------------------------------------
    # コマンド: ボイスチャンネルに参加 (/join)
    # --------------------------------------------------
    @app_commands.command(name="join", description="ボイスチャンネルに参加します")
    async def join(self, interaction: discord.Interaction):
        if interaction.user.voice is None:
            await interaction.response.send_message("❌ まずあなたがボイスチャンネルに入ってください！", ephemeral=True)
            return

        # 3秒ルール対策：考え中...にする
        await interaction.response.defer()

        channel = interaction.user.voice.channel
        # self_deaf=Trueで接続（安定化のため）
        await channel.connect(self_deaf=True)
        # deferした後なので、responseではなく「followup」を使ってメッセージを送る
        await interaction.followup.send(f"🔊 **{channel.name}** に接続しました！")

    # --------------------------------------------------
    # コマンド: 音楽再生 (/play)
    # --------------------------------------------------
    @app_commands.command(name="play", description="YouTubeから音楽を検索して再生します")
    @app_commands.describe(search="曲名またはURL")
    async def play(self, interaction: discord.Interaction, search: str):
        # 1. BotがVCにいるか確認（いなければ入る）
        if interaction.guild.voice_client is None:
            if interaction.user.voice:
                await interaction.user.voice.channel.connect(self_deaf=True)
            else:
                await interaction.response.send_message("❌ Botが参加していません。先にボイスチャンネルに入ってください。", ephemeral=True)
                return

        # 2. 考え中...（検索には時間がかかるため必須）
        await interaction.response.defer()
        
        vc = interaction.guild.voice_client

        # すでに再生中の場合、一旦止める（簡易的な実装）
        if vc.is_playing():
            vc.stop()

        # --- yt-dlpの設定（ここが重要） ---
        ydl_opts = {
            'format': 'bestaudio/best',
            'noplaylist': True,
            'quiet': True, # ログを減らす
        }

        try:
            # YouTubeから情報を取得
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                # 検索キーワードかどうか判定
                if "http" not in search:
                    # URLでない場合、YouTubeで検索して一番上の結果を使う
                    info = ydl.extract_info(f"ytsearch:{search}", download=False)['entries'][0]
                else:
                    # URLの場合
                    info = ydl.extract_info(search, download=False)

            url = info['url']
            title = info['title']

            # --- FFmpegの設定（途切れないための魔法のオプション） ---
            # これがないと再生が数秒で止まることが多いです
            ffmpeg_options = {
                'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
                'options': '-vn'
            }
            
            # 再生ソースの作成
            source = discord.FFmpegPCMAudio(url, **ffmpeg_options)
            
            # 再生開始
            vc.play(source)
            
            await interaction.followup.send(f"▶️ 再生中: **{title}**")

        except Exception as e:
            await interaction.followup.send(f"❌ エラーが発生しました: {e}")
            print(e)

    # --------------------------------------------------
    # コマンド: 停止 (/stop)
    # --------------------------------------------------
    @app_commands.command(name="stop", description="音楽を停止します")
    async def stop(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if vc and vc.is_playing():
            vc.stop()
            await interaction.response.send_message("⏹️ 停止しました")
        else:
            await interaction.response.send_message("❌ 再生していません", ephemeral=True)

    # --------------------------------------------------
    # コマンド: 切断 (/leave)
    # --------------------------------------------------
    @app_commands.command(name="leave", description="切断します")
    async def leave(self, interaction: discord.Interaction):
        if interaction.guild.voice_client is None:
            await interaction.response.send_message("❌ 参加していません", ephemeral=True)
            return
        
        await interaction.guild.voice_client.disconnect()
        await interaction.response.send_message("👋 バイバイ！")

    # --------------------------------------------------
    # コマンド: 一時停止 (/pause)
    # --------------------------------------------------
    @app_commands.command(name="pause", description="音楽を一時停止します")
    async def pause(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        # 再生中なら一時停止
        if vc and vc.is_playing():
            vc.pause()
            await interaction.response.send_message("⏸️ 一時停止しました")
        else:
            await interaction.response.send_message("❌ 現在再生していないか、既に停止しています。", ephemeral=True)

    # --------------------------------------------------
    # コマンド: 再開 (/resume)
    # --------------------------------------------------
    @app_commands.command(name="resume", description="停止した音楽を再開します")
    async def resume(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        # 一時停止中なら再開
        if vc and vc.is_paused():
            vc.resume()
            await interaction.response.send_message("▶️ 再開しました")
        else:
            await interaction.response.send_message("❌ 一時停止していません。", ephemeral=True)

async def setup(bot):
    await bot.add_cog(MusicCog(bot))