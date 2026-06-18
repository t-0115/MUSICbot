# cogs/channel_manager.py
import discord
from discord import app_commands
from discord.ext import commands
import re

# ==========================================
# チャンネル削除確認用モーダル
# ==========================================
class DeleteChannelModal(discord.ui.Modal):
    def __init__(self, target_channels: list[discord.abc.GuildChannel]):
        super().__init__(title='チャンネル削除の最終確認')
        self.target_channels = target_channels
        
        # 選択されたチャンネルの名前をカンマ区切りで初期値として設定
        names_str = ", ".join([c.name for c in target_channels])
        
        self.channels_input = discord.ui.TextInput(
            label='削除対象のチャンネル (編集して除外も可能)',
            style=discord.TextStyle.paragraph,
            default=names_str,
            required=True
        )
        self.add_item(self.channels_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        
        # モーダルのテキスト入力からチャンネル名を取得
        raw_input = self.channels_input.value
        names = [n.strip() for n in re.split(r'[,\s、]+', raw_input) if n.strip()]
        
        # サーバーの全チャンネルを辞書化
        guild_channels = {channel.name: channel for channel in interaction.guild.channels}
        deleted = []
        not_found = []
        failed = []

        for name in names:
            if name not in guild_channels:
                not_found.append(name)
                continue
            
            channel = guild_channels[name]

            try:
                await channel.delete(reason=f"{interaction.user.display_name} による一括削除")
                deleted.append(name)
            except discord.Forbidden:
                failed.append(f"{name} (権限不足)")
            except discord.HTTPException:
                failed.append(f"{name} (エラー)")

        # 結果報告メッセージの組み立て
        msg_lines = []
        if deleted:
            msg_lines.append(f"🗑️ **削除成功**: {', '.join(deleted)}")
        if not_found:
            msg_lines.append(f"⚠️ **見つからなかった**: {', '.join(not_found)}\n*(※テキストチャンネル名特有のハイフン等に変換された名前と一致していない可能性があります)*")
        if failed:
            msg_lines.append(f"❌ **削除失敗**: {', '.join(failed)}")
        
        if not msg_lines:
            msg_lines.append("処理されたチャンネルはありませんでした。")
            
        await interaction.followup.send("\n".join(msg_lines))


# ==========================================
# チャンネル管理 Cog
# ==========================================
class ChannelManagerCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ==========================================
    # チャンネル作成コマンド
    # ==========================================
    @app_commands.command(name='チャンネル作成', description='新しいテキストチャンネルをまとめて作成します')
    @app_commands.describe(
        names='作成するチャンネルの名前（スペース、カンマ、読点で区切って複数作成可能）',
        category='チャンネルを作成するカテゴリ（省略時はこのコマンドを打ったカテゴリを自動指定）'
    )
    async def create_channels(self, interaction: discord.Interaction, names: str, category: discord.CategoryChannel = None):
        await interaction.response.defer(ephemeral=False)
        
        # 入力をリスト化
        channel_names = [n.strip() for n in re.split(r'[,\s、]+', names) if n.strip()]
        
        if not channel_names:
            await interaction.followup.send("❌ 有効なチャンネル名が指定されていません。")
            return

        # 【変更点】カテゴリ指定が省略された場合、現在のチャンネルのカテゴリをデフォルトにする
        if category is None:
            category = getattr(interaction.channel, 'category', None)

        created_names = []
        existing_names = []
        failed = []

        # 現在のチャンネル名をリスト化（重複作成防止用）
        existing_channel_names = [c.name for c in interaction.guild.channels]

        try:
            for ch_name in channel_names:
                if ch_name in existing_channel_names:
                    existing_names.append(ch_name)
                else:
                    try:
                        await interaction.guild.create_text_channel(
                            name=ch_name,
                            category=category,
                            reason=f"{interaction.user.display_name} が作成"
                        )
                        created_names.append(ch_name)
                    except discord.Forbidden:
                        failed.append(f"{ch_name} (権限不足)")
                    except discord.HTTPException:
                        failed.append(f"{ch_name} (エラー)")
            
            # 結果報告メッセージの組み立て
            msg_lines = []
            if created_names:
                category_name = f"「{category.name}」内に" if category else "「カテゴリなし」に"
                msg_lines.append(f"✅ **新規作成** {category_name}: {', '.join(created_names)}")
            if existing_names:
                msg_lines.append(f"⚠️ **既存のため作成スキップ**: {', '.join(existing_names)}")
            if failed:
                msg_lines.append(f"❌ **作成失敗**: {', '.join(failed)}")
            
            await interaction.followup.send("\n".join(msg_lines))
            
        except Exception as e:
            await interaction.followup.send(f"❌ 予期せぬエラーが発生しました: {e}")

    # ==========================================
    # チャンネル削除コマンド（複数引数対応 ＆ モーダル確認）
    # ==========================================
    @app_commands.command(name='チャンネル削除', description='指定したチャンネルをまとめて削除します（実行後に確認画面が出ます）')
    @app_commands.describe(
        channel1='削除するチャンネル1',
        channel2='削除するチャンネル2 (任意)',
        channel3='削除するチャンネル3 (任意)',
        channel4='削除するチャンネル4 (任意)',
        channel5='削除するチャンネル5 (任意)'
    )
    async def delete_channels(
        self, 
        interaction: discord.Interaction, 
        channel1: discord.abc.GuildChannel,
        channel2: discord.abc.GuildChannel = None,
        channel3: discord.abc.GuildChannel = None,
        channel4: discord.abc.GuildChannel = None,
        channel5: discord.abc.GuildChannel = None
    ):
        # 指定されたチャンネル（None以外）をリストにまとめる
        channels = [c for c in [channel1, channel2, channel3, channel4, channel5] if c is not None]
        
        # モーダルを呼び出して最終確認を行う
        await interaction.response.send_modal(DeleteChannelModal(channels))

async def setup(bot):
    await bot.add_cog(ChannelManagerCog(bot))