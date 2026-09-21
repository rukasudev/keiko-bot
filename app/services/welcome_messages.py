import asyncio
import os
from io import BytesIO
from typing import Callable, Dict, List, Optional
from urllib.parse import urlparse

import discord
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from app import logger
from app.components.embed import default_welcome_embed
from app.constants import Commands as constants
from app.constants import LogTypes as logconstants
from app.constants import Style, WelcomeDesign
from app.exceptions import ErrorContext
from app.settings import open_feature
from app.services import analytics, cache, cdn, images
from app.services.utils import parse_welcome_messages


async def manager(interaction: discord.Interaction, guild_id: str) -> None:
    """The slash command: the setup form, or the manager of what is saved."""
    await open_feature(interaction, constants.WELCOME_MESSAGES_KEY)


async def send_welcome_message(member: discord.Member):
    cogs = await asyncio.to_thread(
        cache.get_cog_data_or_populate, member.guild.id, constants.WELCOME_MESSAGES_KEY
    )

    if cogs == None:
        return

    channel_data = cogs.get("welcome_messages_channel")
    welcome_messages_data = cogs.get("welcome_messages")

    if not channel_data or not welcome_messages_data:
        return

    channel = channel_data.get("values")
    welcome_messages = welcome_messages_data.get("values")

    if not channel or not welcome_messages:
        return

    channel = member.guild.get_channel(int(channel))
    if not channel:
        return

    welcome_message_title = cogs["welcome_messages_title"]
    welcome_message_footer = cogs.get("welcome_messages_footer") or ""
    design = cogs.get("welcome_design", "server_blur")
    custom_image = cogs.get("welcome_custom_image")

    welcome_message = parse_welcome_messages(welcome_messages, member)

    context = ErrorContext.from_member(
        flow="welcome_message",
        member=member,
        design=design,
        custom_image=custom_image[:100] if custom_image else None,
    )

    drew_plain = False

    def plain_background() -> None:
        nonlocal drew_plain
        drew_plain = True

    try:
        embed_message = await create_welcome_message(
            member, welcome_message_title, welcome_message, welcome_message_footer,
            design=design, custom_image=custom_image,
            on_plain_background=plain_background,
        )
        await channel.send(embed=embed_message)
        analytics.record_value(
            member.guild.id,
            constants.WELCOME_MESSAGES_KEY,
            outcome="plain_background" if drew_plain else "ok",
        )
    except discord.Forbidden as e:
        analytics.record_permission_failure(
            member.guild.id, constants.WELCOME_MESSAGES_KEY, e
        )
        raise
    except Exception as e:
        logger.error(
            f"Failed to send welcome message: {type(e).__name__}: {e}",
            log_type=logconstants.COMMAND_ERROR_TYPE,
            context=context,
            exc_info=True,
        )
        raise

async def generate_design_previews(member: discord.Member, designs: list) -> dict:
    """One preview url per design, all drawn at the same time."""
    server_icon = str(member.guild.icon.url) if member.guild.icon else None

    def banner(background: Optional[str]):
        return create_banner(
            background, "WELCOME", member.name, member.display_avatar.url, member.guild.name
        )

    generators = {
        "server_blur": lambda: banner(server_icon),
        "custom_blur": lambda: banner(WelcomeDesign.CUSTOM_BLUR_PREVIEW),
        "custom_only": lambda: cdn.upload_asset(_asset_path(WelcomeDesign.CUSTOM_ONLY_PREVIEW)),
    }

    async def preview(key: str):
        try:
            return key, await generators[key]()
        except Exception as e:
            logger.warn(f"Failed to generate preview for {key}: {e}")
            return key, None

    keys = [design["key"] for design in designs if design["key"] in generators]
    results = await asyncio.gather(*(preview(key) for key in keys))
    return {key: url for key, url in results if url}


async def create_welcome_message(
    member: discord.Member,
    title: str,
    message: str,
    footer: str,
    design: str = "server_blur",
    custom_image: str = None,
    on_plain_background: Optional[Callable[[], None]] = None,
):
    if design == "custom_only" and custom_image:
        embed = default_welcome_embed(title, message, footer, custom_image)
        embed.set_thumbnail(url=member.display_avatar.url)
        return embed

    if design == "custom_blur" and custom_image:
        background_url = custom_image
    else:
        background_url = str(member.guild.icon.url) if member.guild.icon else None

    banner = await create_banner(
        background_url, title.upper(), member.name,
        member.display_avatar.url, member.guild.name, on_plain_background
    )
    return default_welcome_embed(title, message, footer, banner)

async def send_welcome_message_preview(interaction: discord.Interaction, response: List[Dict[str, str]]):
    welcome_data = {
        item["key"]: item.get("_raw_value", item.get("value"))
        for item in response
        if item.get("key") in WelcomeDesign.PREVIEW_DATA_KEYS
    }

    if not welcome_data:
        cogs = await asyncio.to_thread(
            cache.get_cog_data_or_populate,
            interaction.guild.id,
            constants.WELCOME_MESSAGES_KEY,
        )
        if not cogs:
            return
        welcome_data = {
            "welcome_messages_title": cogs.get("welcome_messages_title"),
            "welcome_messages": cogs.get("welcome_messages", {}).get("values") if isinstance(cogs.get("welcome_messages"), dict) else cogs.get("welcome_messages"),
            "welcome_messages_footer": cogs.get("welcome_messages_footer"),
            "welcome_design": cogs.get("welcome_design", "server_blur"),
            "welcome_custom_image": cogs.get("welcome_custom_image"),
        }

    title = welcome_data.get("welcome_messages_title")
    messages = welcome_data.get("welcome_messages")
    footer = welcome_data.get("welcome_messages_footer") or ""
    design = welcome_data.get("welcome_design", "server_blur")
    custom_image = welcome_data.get("welcome_custom_image")

    if not title or not messages:
        return

    member = interaction.user
    if not isinstance(member, discord.Member):
        member = interaction.guild.get_member(interaction.user.id)

    if not member:
        return

    welcome_message = parse_welcome_messages(messages, member)
    embed_message = await create_welcome_message(
        member, title, welcome_message, footer,
        design=design, custom_image=custom_image
    )

    await interaction.followup.send(embed=embed_message, ephemeral=True)


def _asset_path(name: str) -> str:
    """A file of this repository, found from the package rather than the cwd."""
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(root, name)


def _plain_background() -> Image.Image:
    return Image.new("RGB", WelcomeDesign.BANNER_SIZE, f"#{Style.BACKGROUND_COLOR}")


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
        return await asyncio.to_thread(_open_asset, _asset_path(source))
    except Exception as e:
        logger.warn(
            f"Welcome banner background unavailable, drawing a plain one: {type(e).__name__}"
        )
        if on_plain is not None:
            on_plain()
        return _plain_background()


def _draw_banner(
    background_img: Image.Image,
    overlay_image: Image.Image,
    welcome_message: str,
    username: str,
    server_name: str,
) -> BytesIO:
    banner_width, banner_height = WelcomeDesign.BANNER_SIZE

    aspect_ratio_banner = banner_width / banner_height
    aspect_ratio_image = background_img.width / background_img.height

    if aspect_ratio_image > aspect_ratio_banner:
        new_height = banner_height
        new_width = int(banner_height * aspect_ratio_image)
    else:
        new_width = banner_width
        new_height = int(banner_width / aspect_ratio_image)

    background_img = background_img.resize((new_width, new_height), Image.LANCZOS)

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
        (circle_center[0] + circle_radius, circle_center[1] + circle_radius)
    ]
    draw.ellipse(circle_bbox, fill="white")

    overlay_image = overlay_image.resize((circle_diameter, circle_diameter), Image.LANCZOS)

    mask = Image.new("L", (circle_diameter, circle_diameter), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.ellipse((0, 0, circle_diameter, circle_diameter), fill=255)

    overlay_image = overlay_image.convert("RGBA")
    overlay_image.putalpha(mask)

    overlay.paste(overlay_image, (circle_center[0] - circle_radius, circle_center[1] - circle_radius), overlay_image)
    draw.ellipse(circle_bbox, outline="black", width=5)

    font_size = 24
    font_path = "./app/assets/fonts/Poppins-Bold.ttf"
    font = ImageFont.truetype(font_path, font_size)

    welcome_message_bbox = draw.textbbox((0, 0), welcome_message, font=font)
    welcome_message_width = welcome_message_bbox[2] - welcome_message_bbox[0]
    welcome_message_height = welcome_message_bbox[3] - welcome_message_bbox[1]
    welcome_message_x = (banner_width - welcome_message_width) // 2
    welcome_message_y = circle_center[1] - circle_radius - welcome_message_height - 20

    draw.text((welcome_message_x, welcome_message_y), welcome_message, font=font, fill="white", stroke_fill="black", stroke_width=1)

    username_bbox = draw.textbbox((0, 0), username, font=font)
    username_width = username_bbox[2] - username_bbox[0]
    username_x = (banner_width - username_width) // 2
    username_y = circle_center[1] + circle_radius + 10

    draw.text((username_x, username_y), username, font=font, fill="white", stroke_fill="black", stroke_width=1)

    server_name_font_size = font_size - 2
    server_name_font = ImageFont.truetype(font_path, server_name_font_size)

    server_name_bbox = draw.textbbox((0, 0), server_name, font=server_name_font)
    server_name_width = server_name_bbox[2] - server_name_bbox[0]
    server_name_x = (banner_width - server_name_width) // 2
    server_name_y = 10

    draw.text((server_name_x, server_name_y), server_name, font=server_name_font, fill="white", stroke_fill="black", stroke_width=1)

    combined = Image.alpha_composite(blurred_background.convert("RGBA"), overlay)

    img_bytes = BytesIO()
    combined.save(img_bytes, format='PNG')
    img_bytes.seek(0)

    return img_bytes


async def create_banner(
    background_url: Optional[str],
    welcome_message: str,
    username: str,
    user_image_url: str,
    server_name: str,
    on_plain_background: Optional[Callable[[], None]] = None,
):
    background_img, overlay_image = await asyncio.gather(
        _background(background_url, on_plain_background),
        images.fetch_image(user_image_url),
    )
    banner = await asyncio.to_thread(
        _draw_banner, background_img, overlay_image, welcome_message, username, server_name
    )
    return await cdn.upload(banner, "banner.png")
