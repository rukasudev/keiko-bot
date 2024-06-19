
import functools
import random
from io import BytesIO

import discord
import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from app import bot
from app.components.embed import default_welcome_embed
from app.constants import Commands as constants
from app.services import cache
from app.services.moderations import (
    send_command_form_message,
    send_command_manager_message,
)
from app.services.utils import parse_welcome_message, split_welcome_messages


async def manager(interaction: discord.Interaction, guild_id: str):
    cogs = cache.get_cog_data_or_populate(guild_id, constants.WELCOME_MESSAGES_KEY)

    if cogs == None:
        return await send_command_form_message(interaction, constants.WELCOME_MESSAGES_KEY)

    await send_command_manager_message(
        interaction, constants.WELCOME_MESSAGES_KEY, cogs
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

    splited_messages = split_welcome_messages(welcome_messages)
    random_message = random.choice(splited_messages)
    welcome_message = parse_welcome_message(random_message, member)

    welcome_message_title = cogs["welcome_messages_title"]
    welcome_message_footer = cogs["welcome_messages_footer"]

    banner = await create_banner(member.guild.icon.url, welcome_message_title.upper(), member.name, member.avatar.url, member.guild.name)
    embed_message = default_welcome_embed(welcome_message_title, welcome_message, welcome_message_footer, banner)

    await channel.send(embed=embed_message)

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

    blurred_background = background_img.filter(ImageFilter.BLUR)
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
