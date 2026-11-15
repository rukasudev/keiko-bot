"""Welcome messages: the banner and message a new member gets, and their previews."""

import asyncio
import os
import random
from collections.abc import Mapping
from dataclasses import dataclass, replace
from io import BytesIO
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

import discord
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from app import logger
from app.constants import Commands as constants
from app.constants import LogTypes as logconstants
from app.constants import Style
from app.constants import ViewConstants as view_constants
from app.core import analytics, cache, images, members
from app.core.commands import MemberJoined
from app.core.threads import to_thread
from app.core.utils import fill_placeholders, ml, split_messages, values_of
from app.exceptions import ErrorContext
from app.forms import open_feature
from app.forms.features.feature import (
    AsideAction,
    GenericCogFeature,
    OpenContext,
    Opened,
)
from app.forms.form.components import Button
from app.ui.embed import announcement_embed
from app.ui.message_preview import MessagePreviewView


@dataclass(frozen=True)
class WelcomeSettings:
    """The title, footer and design every welcome message of a server shares."""

    title: str
    footer: str
    design: str
    custom_image: Optional[str]


async def manager(interaction: discord.Interaction) -> None:
    """The slash command: the setup form, or the manager of what is saved."""
    await open_feature(interaction, "welcome_messages")


def render_welcome_message(welcome_message: str, member: discord.Member) -> str:
    """A welcome template filled for a member, its mention added without `{user}`."""
    return fill_placeholders(
        welcome_message,
        {
            "server": member.guild.name,
            "member_count": str(member.guild.member_count),
            "user": f"<@!{member._user.id}>",
        },
        required="user",
    )


def parse_welcome_messages(welcome_messages: Any, member: discord.Member) -> str:
    """One of the saved welcome messages, picked at random and filled for `member`."""
    messages = split_messages(welcome_messages) or [""]

    return render_welcome_message(random.choice(messages), member)


async def greet(joined: MemberJoined) -> None:
    """Welcome the member who joined."""
    await send_welcome_message(joined.member)


async def send_welcome_message(member: discord.Member) -> None:
    """Welcome a member who just joined in the channel the server chose."""
    cogs = await cache.read_cog_data(member.guild.id, "welcome_messages")

    if cogs is None:
        return

    channel_data = cogs.get("welcome_messages_channel")
    welcome_messages_data = cogs.get("welcome_messages")

    if not channel_data or not welcome_messages_data:
        return

    channel_id = channel_data.get("values")
    welcome_messages = welcome_messages_data.get("values")

    if not channel_id or not welcome_messages:
        return

    channel = member.guild.get_channel(int(channel_id))
    if channel is None or isinstance(
        channel, (discord.ForumChannel, discord.CategoryChannel)
    ):
        return

    settings = WelcomeSettings(
        title=cogs["welcome_messages_title"],
        footer=cogs.get("welcome_messages_footer") or "",
        design=cogs.get("welcome_design", "server_blur"),
        custom_image=cogs.get("welcome_custom_image"),
    )
    welcome_message = parse_welcome_messages(welcome_messages, member)

    context = ErrorContext.from_member(
        flow="welcome_message",
        member=member,
        design=settings.design,
        custom_image=settings.custom_image[:100] if settings.custom_image else None,
    )

    drew_plain = False

    def plain_background() -> None:
        nonlocal drew_plain
        drew_plain = True

    attaches = channel.permissions_for(member.guild.me).attach_files
    linked = not attaches and not _custom_image_shown(settings)
    mentions = discord.AllowedMentions(everyone=False, roles=False, users=[member])
    try:
        embed_message, banner = await create_welcome_message(
            member,
            welcome_message,
            settings,
            on_plain_background=plain_background,
            attach=attaches,
        )
        if banner is None:
            await channel.send(embed=embed_message, allowed_mentions=mentions)
        else:
            await channel.send(
                embed=embed_message, file=banner, allowed_mentions=mentions
            )

        if drew_plain:
            outcome = "plain_background"
        else:
            outcome = "no_attach_files" if linked else "ok"
        analytics.record_value(member.guild.id, "welcome_messages", outcome=outcome)
    except discord.Forbidden as error:
        analytics.record_permission_failure(member.guild.id, "welcome_messages", error)
        raise
    except Exception as error:
        logger.error(
            f"Failed to send welcome message: {type(error).__name__}: {error}",
            log_type=logconstants.COMMAND_ERROR_TYPE,
            context=context,
            exc_info=True,
        )
        raise


async def generate_design_previews(
    member: discord.Member,
    designs: Sequence[str],
    title: str,
    background: Optional[str] = None,
) -> Dict[str, str]:
    """One preview url per design, drawn with `title` over `background` when sent."""
    server_icon = str(member.guild.icon.url) if member.guild.icon else None

    def banner(background_url: Optional[str]) -> Awaitable[str]:
        return create_banner(background_url, title, member)

    async def as_sent() -> Optional[str]:
        return background

    generators: Dict[str, Callable[[], Awaitable[Optional[str]]]] = {
        "server_blur": lambda: banner(server_icon),
        "custom_blur": lambda: banner(
            background or "app/assets/welcome/custom_blur_preview.jpg"
        ),
        "custom_only": (
            as_sent
            if background
            else lambda: images.upload_asset(
                _asset_path("app/assets/welcome/custom_only_preview.gif")
            )
        ),
    }

    async def preview(key: str) -> Tuple[str, Optional[str]]:
        try:
            return key, await generators[key]()
        except Exception as error:
            logger.warn(f"Failed to generate preview for {key}: {error}")
            return key, None

    keys = [design for design in designs if design in generators]
    results = await asyncio.gather(*(preview(key) for key in keys))
    return {key: url for key, url in results if url}


async def create_welcome_message(
    member: discord.Member,
    message: str,
    settings: WelcomeSettings,
    on_plain_background: Optional[Callable[[], None]] = None,
    attach: bool = True,
) -> Tuple[discord.Embed, Optional[discord.File]]:
    """The embed a member receives, its banner attached, or linked when not `attach`."""
    title, footer = settings.title, settings.footer
    custom_image = _custom_image_shown(settings)
    if custom_image:
        return _custom_image_embed(member, title, message, footer, custom_image), None

    background = _banner_background(member, settings)
    if not attach:
        link = await create_banner(background, title, member, on_plain_background)
        return announcement_embed(title, message, footer, link), None

    banner = await draw_banner(background, title, member, on_plain_background)
    attachment = f"attachment://{constants.WELCOME_BANNER_FILENAME}"
    return (
        announcement_embed(title, message, footer, attachment),
        discord.File(banner, filename=constants.WELCOME_BANNER_FILENAME),
    )


async def welcome_preview_pages(
    member: discord.Member, messages: List[str], settings: WelcomeSettings
) -> List[discord.Embed]:
    """One embed per written message, all over one banner drawn before any click."""
    title, footer = settings.title, settings.footer
    texts = [render_welcome_message(message, member) for message in (messages or [""])]

    custom_image = _custom_image_shown(settings)
    if custom_image:
        return [
            _custom_image_embed(member, title, text, footer, custom_image)
            for text in texts
        ]

    background = _banner_background(member, settings)
    banner = await create_banner(background, title, member)
    return [announcement_embed(title, text, footer, banner) for text in texts]


def _custom_image_shown(settings: WelcomeSettings) -> Optional[str]:
    return settings.custom_image if settings.design == "custom_only" else None


def _custom_image_embed(
    member: discord.Member, title: str, message: str, footer: str, custom_image: str
) -> discord.Embed:
    embed = announcement_embed(title, message, footer, custom_image)
    embed.set_thumbnail(url=member.display_avatar.url)
    return embed


def _banner_background(
    member: discord.Member, settings: WelcomeSettings
) -> Optional[str]:
    if settings.design == "custom_blur" and settings.custom_image:
        return settings.custom_image
    return str(member.guild.icon.url) if member.guild.icon else None


async def send_welcome_message_preview(
    interaction: discord.Interaction, response: List[Dict[str, Any]]
) -> None:
    """The welcome message previewed for the admin, from the answers or the save."""
    guild = interaction.guild
    if guild is None:
        return

    welcome_data = values_of(
        response,
        (
            "welcome_messages_title",
            "welcome_messages",
            "welcome_messages_footer",
            "welcome_design",
            "welcome_custom_image",
        ),
    )

    if not welcome_data:
        cogs = await cache.read_cog_data(guild.id, "welcome_messages")
        if not cogs:
            return

        saved_messages = cogs.get("welcome_messages")
        welcome_data = {
            "welcome_messages_title": cogs.get("welcome_messages_title"),
            "welcome_messages": (
                saved_messages.get("values")
                if isinstance(saved_messages, dict)
                else saved_messages
            ),
            "welcome_messages_footer": cogs.get("welcome_messages_footer"),
            "welcome_design": cogs.get("welcome_design", "server_blur"),
            "welcome_custom_image": cogs.get("welcome_custom_image"),
        }

    title = welcome_data.get("welcome_messages_title")
    messages = welcome_data.get("welcome_messages")
    if not title or not messages:
        return

    member: Optional[discord.Member] = (
        interaction.user
        if isinstance(interaction.user, discord.Member)
        else await members.find(guild, interaction.user.id)
    )
    if not member:
        return

    settings = WelcomeSettings(
        title=title,
        footer=welcome_data.get("welcome_messages_footer") or "",
        design=welcome_data.get("welcome_design", "server_blur"),
        custom_image=welcome_data.get("welcome_custom_image"),
    )
    pages = await welcome_preview_pages(member, split_messages(messages), settings)
    await MessagePreviewView(pages, interaction.locale).send(interaction)


def _asset_path(name: str) -> str:
    """A file of this repository, found from the package rather than the cwd."""
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(os.path.dirname(os.path.dirname(here)))
    return os.path.join(root, name)


def _plain_background() -> Image.Image:
    return Image.new("RGB", constants.WELCOME_BANNER_SIZE, f"#{Style.BACKGROUND_COLOR}")


def _open_asset(path: str) -> Image.Image:
    image = Image.open(path)
    image.load()
    return image


async def _background(
    source: Optional[str], on_plain: Optional[Callable[[], None]] = None
) -> Image.Image:
    if source is None:
        return _plain_background()

    try:
        if urlparse(source).scheme in ("http", "https"):
            return await images.fetch_image(source)
        return await to_thread(_open_asset, _asset_path(source))
    except Exception as error:
        logger.warn(
            "Welcome banner background unavailable, drawing a plain one: "
            f"{type(error).__name__}: {error}"
        )
        if on_plain is not None:
            on_plain()
        return _plain_background()


def _paint_banner(
    background_img: Image.Image,
    overlay_image: Image.Image,
    welcome_message: str,
    username: str,
    server_name: str,
) -> BytesIO:
    banner_width, banner_height = constants.WELCOME_BANNER_SIZE

    aspect_ratio_banner = banner_width / banner_height
    aspect_ratio_image = background_img.width / background_img.height

    if aspect_ratio_image > aspect_ratio_banner:
        new_height = banner_height
        new_width = int(banner_height * aspect_ratio_image)
    else:
        new_width = banner_width
        new_height = int(banner_width / aspect_ratio_image)

    background_img = background_img.resize(
        (new_width, new_height), Image.Resampling.LANCZOS
    )

    left = (new_width - banner_width) / 2
    top = (new_height - banner_height) / 2
    right = (new_width + banner_width) / 2
    bottom = (new_height + banner_height) / 2
    background_img = background_img.crop((left, top, right, bottom)).convert("RGBA")

    blurred_background = background_img.filter(ImageFilter.GaussianBlur(5))
    overlay = Image.new("RGBA", (banner_width, banner_height), (255, 255, 255, 0))

    draw = ImageDraw.Draw(overlay)
    circle_diameter = min(banner_width, banner_height) // 2
    circle_radius = circle_diameter // 2
    circle_center = (banner_width // 2, banner_height // 2)
    circle_bbox = [
        (circle_center[0] - circle_radius, circle_center[1] - circle_radius),
        (circle_center[0] + circle_radius, circle_center[1] + circle_radius),
    ]
    draw.ellipse(circle_bbox, fill="white")

    overlay_image = overlay_image.resize(
        (circle_diameter, circle_diameter), Image.Resampling.LANCZOS
    )

    mask = Image.new("L", (circle_diameter, circle_diameter), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.ellipse((0, 0, circle_diameter, circle_diameter), fill=255)

    overlay_image = overlay_image.convert("RGBA")
    overlay_image.putalpha(mask)

    overlay.paste(
        overlay_image,
        (circle_center[0] - circle_radius, circle_center[1] - circle_radius),
        overlay_image,
    )
    draw.ellipse(circle_bbox, outline="black", width=5)

    font_size = 24
    font_path = "./app/assets/fonts/Poppins-Bold.ttf"
    font = ImageFont.truetype(font_path, font_size)

    welcome_message_bbox = draw.textbbox((0, 0), welcome_message, font=font)
    welcome_message_width = welcome_message_bbox[2] - welcome_message_bbox[0]
    welcome_message_height = welcome_message_bbox[3] - welcome_message_bbox[1]
    welcome_message_x = (banner_width - welcome_message_width) // 2
    welcome_message_y = circle_center[1] - circle_radius - welcome_message_height - 20

    draw.text(
        (welcome_message_x, welcome_message_y),
        welcome_message,
        font=font,
        fill="white",
        stroke_fill="black",
        stroke_width=1,
    )

    username_bbox = draw.textbbox((0, 0), username, font=font)
    username_width = username_bbox[2] - username_bbox[0]
    username_x = (banner_width - username_width) // 2
    username_y = circle_center[1] + circle_radius + 10

    draw.text(
        (username_x, username_y),
        username,
        font=font,
        fill="white",
        stroke_fill="black",
        stroke_width=1,
    )

    server_name_font_size = font_size - 2
    server_name_font = ImageFont.truetype(font_path, server_name_font_size)

    server_name_bbox = draw.textbbox((0, 0), server_name, font=server_name_font)
    server_name_width = server_name_bbox[2] - server_name_bbox[0]
    server_name_x = (banner_width - server_name_width) // 2
    server_name_y = 10

    draw.text(
        (server_name_x, server_name_y),
        server_name,
        font=server_name_font,
        fill="white",
        stroke_fill="black",
        stroke_width=1,
    )

    combined = Image.alpha_composite(blurred_background.convert("RGBA"), overlay)

    img_bytes = BytesIO()
    combined.save(img_bytes, format="PNG")
    img_bytes.seek(0)

    return img_bytes


async def draw_banner(
    background_url: Optional[str],
    title: str,
    member: discord.Member,
    on_plain_background: Optional[Callable[[], None]] = None,
) -> BytesIO:
    """`title` over `background_url`, around the picture and name of `member`."""
    background_img, overlay_image = await asyncio.gather(
        _background(background_url, on_plain_background),
        images.fetch_image(
            member.display_avatar.with_size(constants.WELCOME_AVATAR_SIZE).url
        ),
    )

    return await to_thread(
        _paint_banner,
        background_img,
        overlay_image,
        title.upper(),
        member.name,
        member.guild.name,
    )


async def create_banner(
    background_url: Optional[str],
    title: str,
    member: discord.Member,
    on_plain_background: Optional[Callable[[], None]] = None,
) -> str:
    """The banner uploaded to the dump channel, for a screen that shows it by url."""
    banner = await draw_banner(background_url, title, member, on_plain_background)
    return await images.upload(banner, constants.WELCOME_BANNER_FILENAME)


class WelcomeMessagesFeature(GenericCogFeature):
    """A personalized banner and message for every new member."""

    def __init__(self) -> None:
        super().__init__("welcome_messages")

    async def open(self, context: OpenContext) -> Opened:
        """The document with the preview button, or the previews for a setup."""
        opened = await super().open(context)
        if opened.document is not None:
            title = str(opened.document.get("welcome_messages_title") or "")
            return replace(opened, pending_previews=self.previews(context, title))
        return Opened(pending_previews=self.previews(context))

    async def previews(
        self, context: OpenContext, title: str = "", background: str = ""
    ) -> Mapping[str, str]:
        """One rendered banner per design, drawn with the saved title while the
        member answers the first steps, over the sent picture when there is one."""
        designs = [design.value for design in self.definition.designs()]
        if not designs or context.member is None:
            return {}
        try:
            drawn = await generate_design_previews(
                context.member,
                designs,
                title or ml("welcome_messages.messages.banner_title", context.locale),
                background or None,
            )
            return dict(drawn)
        except Exception:
            return {}

    async def previews_for(
        self, values: Mapping[str, Any], context: OpenContext
    ) -> Mapping[str, str]:
        """The banners drawn again, now over the picture the admin just sent."""
        return await self.previews(
            context,
            str(values.get("welcome_messages_title") or ""),
            str(values.get("welcome_custom_image") or ""),
        )

    def extra_buttons(self, context: OpenContext) -> tuple[Button, ...]:
        """The preview button, worded by the shared Preview button."""
        return (
            self.panel_button("preview", "👁️", context.locale, copy="buttons.preview"),
        )

    def asides(self) -> Mapping[str, AsideAction]:
        """The statistics, and the welcome message previewed for the admin themself."""

        async def preview(interaction: Any, responses: Any) -> None:
            await send_welcome_message_preview(interaction, list(responses))

        return {
            **super().asides(),
            "preview": AsideAction(
                preview, defer=True, cooldown=view_constants.ACTION_COOLDOWN_SECONDS
            ),
        }


FEATURE = WelcomeMessagesFeature()
