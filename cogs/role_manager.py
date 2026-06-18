# cogs/role_manager.py
import discord
from discord import app_commands
from discord.ext import commands
import re

# ==========================================
# ロール削除確認用モーダル
# ==========================================
class DeleteRoleModal(discord.ui.Modal):
    def __init__(self, target_roles: list[discord.Role]):
        super().__init__(title='ロール削除の最終確認')
        self.target_roles = target_roles
        
        # 選択されたロールの名前をカンマ区切りで初期値として設定
        names_str = ", ".join([r.name for r in target_roles])
        
        self.roles_input = discord.ui.TextInput(
            label='削除対象のロール (編集して除外も可能)',
            style=discord.TextStyle.paragraph,
            default=names_str,
            required=True
        )
        self.add_item(self.roles_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        
        # モーダルのテキスト入力からロール名を取得
        raw_input = self.roles_input.value
        names = [n.strip() for n in re.split(r'[,\s、]+', raw_input) if n.strip()]
        
        guild_roles = {role.name: role for role in interaction.guild.roles}
        deleted = []
        not_found = []
        failed = []

        for name in names:
            if name not in guild_roles:
                not_found.append(name)
                continue
            
            role = guild_roles[name]
            
            # デフォルトロールやBotシステムロールは弾く
            if role.is_default() or role.managed:
                failed.append(f"{name} (システム用)")
                continue

            try:
                await role.delete(reason=f"{interaction.user.display_name} による一括削除")
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
            msg_lines.append(f"⚠️ **見つからなかった**: {', '.join(not_found)}")
        if failed:
            msg_lines.append(f"❌ **削除失敗**: {', '.join(failed)}")
        
        if not msg_lines:
            msg_lines.append("処理されたロールはありませんでした。")
            
        await interaction.followup.send("\n".join(msg_lines))


# ==========================================
# ロール管理 Cog
# ==========================================
class RoleManagerCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ==========================================
    # リアクションからロールIDを抽出するヘルパー関数
    # ==========================================
    def _extract_role_id(self, embed: discord.Embed, emoji_str: str) -> int | None:
        """Embedの「ロール一覧」フィールドから、押された絵文字に対応するロールIDを探す"""
        for field in embed.fields:
            if field.name == "ロール一覧":
                for line in field.value.split('\n'):
                    # 行が対象の絵文字から始まっているか確認
                    if line.strip().startswith(emoji_str):
                        # <@&1234567890> のようなメンションから数字(ID)だけを抽出
                        match = re.search(r'<@&(\d+)>', line)
                        if match:
                            return int(match.group(1))
        return None

    # ==========================================
    # イベント: リアクションが追加された時（ロール付与）
    # ==========================================
    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        # Bot自身のリアクションは無視
        if payload.user_id == self.bot.user.id:
            return

        # サーバー内での出来事か確認
        if not payload.guild_id:
            return

        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return

        channel = guild.get_channel(payload.channel_id)
        if not channel:
            return

        # Botの再起動後でも取得できるようにfetch_messageを使用
        try:
            message = await channel.fetch_message(payload.message_id)
        except discord.NotFound:
            return

        # 対象の「ロール参加受付」メッセージか判定
        if message.author.id != self.bot.user.id or not message.embeds:
            return
        
        embed = message.embeds[0]
        if embed.title != "🔰 ロール参加受付":
            return

        # 押された絵文字に対応するロールIDを取得して付与
        role_id = self._extract_role_id(embed, str(payload.emoji))
        if role_id:
            role = guild.get_role(role_id)
            member = payload.member
            if role and member:
                try:
                    await member.add_roles(role)
                except discord.Forbidden:
                    pass

    # ==========================================
    # イベント: リアクションが外された時（ロール剥奪）
    # ==========================================
    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        # サーバー内での出来事か確認
        if not payload.guild_id:
            return

        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return

        channel = guild.get_channel(payload.channel_id)
        if not channel:
            return

        try:
            message = await channel.fetch_message(payload.message_id)
        except discord.NotFound:
            return

        if message.author.id != self.bot.user.id or not message.embeds:
            return
        
        embed = message.embeds[0]
        if embed.title != "🔰 ロール参加受付":
            return

        # 絵文字に対応するロールIDを取得して剥奪
        role_id = self._extract_role_id(embed, str(payload.emoji))
        if role_id:
            role = guild.get_role(role_id)
            member = guild.get_member(payload.user_id) # remove時はpayload.memberが使えないため再取得
            if role and member:
                try:
                    await member.remove_roles(role)
                except discord.Forbidden:
                    pass

    # ==========================================
    # ロール作成コマンド
    # ==========================================
    @app_commands.command(name='ロール作成', description='新しいロールを作成し、リアクションロール用メッセージを投稿します')
    @app_commands.describe(
        names='作成するロールの名前（スペース、カンマ、読点で区切って複数作成可能）',
        color_hex='ロールの色（例: #FF0000）※省略可'
    )
    async def create_role(self, interaction: discord.Interaction, names: str, color_hex: str = None):
        await interaction.response.defer(ephemeral=False)
        
        color = discord.Color.default()
        if color_hex:
            try:
                color = discord.Color(int(color_hex.lstrip('#'), 16))
            except ValueError:
                pass

        role_names = [n.strip() for n in re.split(r'[,\s、]+', names) if n.strip()]
        
        if not role_names:
            await interaction.followup.send("❌ 有効なロール名が指定されていません。")
            return

        if len(role_names) > 10:
            await interaction.followup.send("❌ 一度にパネルに追加できるロールは最大10個までです。")
            return

        emojis = ['1️⃣', '2️⃣', '3️⃣', '4️⃣', '5️⃣', '6️⃣', '7️⃣', '8️⃣', '9️⃣', '🔟']
        
        target_roles = []
        created_names = []
        existing_names = []

        try:
            for role_name in role_names:
                existing_role = discord.utils.get(interaction.guild.roles, name=role_name)
                
                if existing_role:
                    existing_names.append(role_name)
                    target_roles.append(existing_role) 
                else:
                    role = await interaction.guild.create_role(
                        name=role_name, 
                        color=color, 
                        reason=f"{interaction.user.display_name} が作成"
                    )
                    created_names.append(role_name)
                    target_roles.append(role)
            
            embed = discord.Embed(
                title="🔰 ロール参加受付",
                description="以下のボタン(リアクション)を押すと、対応するロールが付与されます。\nもう一度押してリアクションを外すと、ロールが解除されます。",
                color=color
            )
            
            description_lines = []
            for i, role in enumerate(target_roles):
                emoji = emojis[i] if len(target_roles) > 1 else '✅'
                description_lines.append(f"{emoji} : {role.mention}")
                
            embed.add_field(name="ロール一覧", value="\n".join(description_lines), inline=False)
            
            msg = await interaction.channel.send(embed=embed)
            
            for i in range(len(target_roles)):
                emoji = emojis[i] if len(target_roles) > 1 else '✅'
                await msg.add_reaction(emoji)
            
            msg_lines = []
            if created_names:
                msg_lines.append(f"✅ **新規作成**: {', '.join(created_names)}")
            if existing_names:
                msg_lines.append(f"⚠️ **既存のため作成スキップ (パネルには追加済)**: {', '.join(existing_names)}")
            
            msg_lines.append("\nリアクションロール用メッセージを投稿しました！")
            
            await interaction.followup.send("\n".join(msg_lines))
            
        except discord.Forbidden:
            await interaction.followup.send("❌ ロールを作成する、またはメッセージを送信する権限がありません。")
        except Exception as e:
            await interaction.followup.send(f"❌ エラーが発生しました: {e}")

    # ==========================================
    # ロール削除コマンド（複数引数対応 ＆ モーダル確認）
    # ==========================================
    @app_commands.command(name='ロール削除', description='指定したロールをまとめて削除します（実行後に確認画面が出ます）')
    @app_commands.describe(
        role1='削除するロール1',
        role2='削除するロール2 (任意)',
        role3='削除するロール3 (任意)',
        role4='削除するロール4 (任意)',
        role5='削除するロール5 (任意)'
    )
    async def delete_roles(
        self, 
        interaction: discord.Interaction, 
        role1: discord.Role,
        role2: discord.Role = None,
        role3: discord.Role = None,
        role4: discord.Role = None,
        role5: discord.Role = None
    ):
        # 指定されたロール（None以外）をリストにまとめる
        roles = [r for r in [role1, role2, role3, role4, role5] if r is not None]
        
        # モーダルを呼び出して最終確認を行う
        await interaction.response.send_modal(DeleteRoleModal(roles))

async def setup(bot):
    await bot.add_cog(RoleManagerCog(bot))