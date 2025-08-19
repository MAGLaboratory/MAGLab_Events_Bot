import requests
from bs4 import BeautifulSoup
import os
import platform
from PIL import Image
import logging

# Set up logging
logging.basicConfig(
    filename='synoptic_view_image_errors.log', level=logging.ERROR,
    format='%(asctime)s %(levelname)s:%(message)s'
)

# If Windows, provide Cairo DLL path for cairosvg
if platform.system() == "Windows":
    os.environ['PATH'] += r';C:\\Program Files\\UniConvertor-2.0rc5\\dlls'

import cairosvg


def scrape_svg(url, svg_id):
    try:
        response = requests.get(url)
        soup = BeautifulSoup(response.content, 'lxml')
        svg_element = soup.find('svg', {'id': svg_id})
        if svg_element:
            return str(svg_element)
        else:
            logging.error(f"SVG with ID {svg_id} not found on the page.")
            return None
    except Exception as e:
        logging.error(f"Error while scraping SVG: {e}")
        return None


def ensure_emoji_font(svg_content):
    try:
        svg_content = svg_content.replace(
            'font-family:DejaVu Sans, sans-serif;',
            'font-family:DejaVu Sans, Noto Emoji, sans-serif;'
        )
        return svg_content
    except Exception as e:
        logging.error(f"Error while ensuring emoji font: {e}")
        return svg_content


def save_scaled_png(svg_content, scaled_png_file, crop_box=(180, 72, 1000, 540), target_width=880, target_height=352):
    try:
        width = "1000"
        height = "1000"
        svg_with_size = f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">\n' + svg_content + '</svg>'
        svg_with_size = ensure_emoji_font(svg_with_size)

        temp_png_file = 'temp_image.png'
        cairosvg.svg2png(bytestring=svg_with_size.encode('utf-8'), write_to=temp_png_file)

        with Image.open(temp_png_file) as img:
            cropped_img = img.crop(crop_box)
            resized_img = cropped_img.resize((target_width, target_height))
            resized_img.save(scaled_png_file)
        os.remove(temp_png_file)

    except Exception as e:
        logging.error(f"Error while saving scaled PNG: {e}")


def generate_scaled_cropped_synoptic_view_image(
    output_png_file, url='https://www.maglaboratory.org/hal', svg_id='maglab-synoptic-view'
):
    """
    Generate the scaled PNG file from the synoptic SVG on the MAGLab site.
    Used by both bots; single-image policy is enforced in the callers.
    """
    try:
        svg_content = scrape_svg(url, svg_id)
        if svg_content:
            save_scaled_png(svg_content, output_png_file)
        else:
            logging.error("Failed to generate PNG. SVG content not found.")
    except Exception as e:
        logging.error(f"Error in generate_scaled_cropped_synoptic_view_image: {e}")


if __name__ == '__main__':
    try:
        generate_scaled_cropped_synoptic_view_image('maglab_synoptic_view_scaled.png')
    except Exception as e:
        logging.error(f"Error while running the script: {e}")
