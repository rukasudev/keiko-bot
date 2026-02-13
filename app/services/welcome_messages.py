import functools
from io import BytesIO
from typing import Dict, List

import discord
import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from app import bot, logger
from app.components.buttons import PreviewButton
from app.components.embed import default_welcome_embed
from app.constants import Commands as constants
from app.constants import WelcomeDesign
from app.services import cache
from app.services.moderations import (
    send_command_form_message,
    send_command_manager_message,
)
from app.services.utils import parse_welcome_messages


async def manager(interaction: discord.Interaction, guild_id: str):
    cogs = cache.get_cog_data_or_populate(guild_id, constants.WELCOME_MESSAGES_KEY, manager=True)

    if cogs == None:
        return await send_command_form_message(interaction, constants.WELCOME_MESSAGES_KEY)

    preview_button = PreviewButton(custom_callback=send_welcome_message_preview, locale=interaction.locale, command_key=constants.WELCOME_MESSAGES_KEY)

    await send_command_manager_message(
        interaction, constants.WELCOME_MESSAGES_KEY, cogs, "", [preview_button]
    )

async def send_welcome_message(member: discord.Member):
    cogs = cache.get_cog_data_or_populate(member.guild.id, constants.WELCOME_MESSAGES_KEY)

    if cogs == None:
        return

    channel = cogs["welcome_messages_channel"].get("values")
    welcome_messages = cogs["welcome_messages"].get("values")

    if not channel and welcome_messages:
        return

    channel = member.guild.get_channel(int(channel))
    if not channel:
        return

    welcome_message_title = cogs["welcome_messages_title"]
    welcome_message_footer = cogs.get("welcome_messages_footer") or ""
    design = cogs.get("welcome_design", "server_blur")
    custom_image = cogs.get("welcome_custom_image")

    welcome_message = parse_welcome_messages(welcome_messages, member)

    embed_message = await create_welcome_message(
        member, welcome_message_title, welcome_message, welcome_message_footer,
        design=design, custom_image=custom_image
    )

    await channel.send(embed=embed_message)

async def generate_design_previews(member: discord.Member, designs: list) -> dict:
    """Generate preview images for each design option in real-time.

    Returns a dict mapping design key to preview URL.
    """
    previews = {}
    server_icon = str(member.guild.icon.url) if member.guild.icon else WelcomeDesign.DEFAULT_ICON

    async def banner_with_bg(bg_url):
        return await create_banner(bg_url, "WELCOME", member.name, member.display_avatar.url, member.guild.name)

    generators = {
        "server_blur": lambda: banner_with_bg(server_icon),
        "custom_blur": lambda: banner_with_bg(WelcomeDesign.CUSTOM_BLUR_PREVIEW),
        "custom_only": lambda: WelcomeDesign.CUSTOM_ONLY_PREVIEW,
    }

    for design in designs:
        key = design["key"]
        generator = generators.get(key)
        if not generator:
            continue

        try:
            result = generator()
            previews[key] = await result if hasattr(result, '__await__') else result
        except Exception as e:
            logger.warn(f"Failed to generate preview for {key}: {e}")

    return previews


async def create_welcome_message(
    member: discord.Member,
    title: str,
    message: str,
    footer: str,
    design: str = "server_blur",
    custom_image: str = None
):
    if design == "custom_only" and custom_image:
        embed = default_welcome_embed(title, message, footer, custom_image)
        embed.set_thumbnail(url=member.display_avatar.url)
        return embed

    if design == "custom_blur" and custom_image:
        background_url = custom_image
    else:
        background_url = str(member.guild.icon.url) if member.guild.icon else WelcomeDesign.DEFAULT_ICON

    banner = await create_banner(
        background_url, title.upper(), member.name,
        member.display_avatar.url, member.guild.name
    )
    return default_welcome_embed(title, message, footer, banner)

async def send_welcome_message_preview(interaction: discord.Interaction, response: List[Dict[str, str]]):
    welcome_data = {
        item["key"]: item.get("_raw_value", item.get("value"))
        for item in response
        if item.get("key") in WelcomeDesign.PREVIEW_DATA_KEYS
    }

    if not welcome_data:
        cogs = cache.get_cog_data_or_populate(interaction.guild.id, constants.WELCOME_MESSAGES_KEY)
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


@functools.cache
def request_image_url(url: str):
    response = requests.get(url)
    image = Image.open(BytesIO(response.content))

    return image

async def create_banner(background_url: str, welcome_message: str, username: str, user_image_url: str, server_name: str):
    background_img = request_image_url(background_url)

    banner_width = 800
    banner_height = 400

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

    overlay_image = request_image_url(user_image_url)
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

    dump_channel = bot.get_channel(bot.config.ADMIN_DUMP_CHANNEL_ID)
    message = await dump_channel.send(file=discord.File(img_bytes))

    return message.attachments[0].url
