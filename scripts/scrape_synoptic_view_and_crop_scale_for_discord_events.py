import requests
from bs4 import BeautifulSoup
import os
import platform
from PIL import Image

# Check if the system is Windows and update the PATH environment variable
if platform.system() == "Windows":
    os.environ['PATH'] += r';C:\Program Files\UniConvertor-2.0rc5\dlls'

import cairosvg

def scrape_svg(url, svg_id):
    # Fetch the webpage content
    response = requests.get(url)
    soup = BeautifulSoup(response.content, 'lxml')

    # Find the SVG element by its ID
    svg_element = soup.find('svg', {'id': svg_id})
    if svg_element:
        return str(svg_element)
    else:
        print(f"SVG with ID {svg_id} not found on the page.")
        return None


def ensure_emoji_font(svg_content):
    # Add fallback for emoji-supporting fonts
    svg_content = svg_content.replace(
        'font-family:DejaVu Sans, sans-serif;',
        'font-family:DejaVu Sans, Noto Emoji, sans-serif;'
    )
    return svg_content


def save_scaled_png(svg_content, scaled_png_file, crop_box=(180, 72, 1000, 540), target_width=880, target_height=352):
    # Define default width and height for the SVG
    width = "1000"
    height = "1000"

    # Add width and height to the SVG content if not present
    svg_with_size = f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">\n' + svg_content + '</svg>'

    # Ensure the emoji font is included
    svg_with_size = ensure_emoji_font(svg_with_size)

    # Convert SVG to PNG using CairoSVG
    temp_png_file = 'temp_image.png'
    cairosvg.svg2png(bytestring=svg_with_size.encode('utf-8'), write_to=temp_png_file)

    # Crop and resize the PNG
    with Image.open(temp_png_file) as img:
        cropped_img = img.crop(crop_box)
        resized_img = cropped_img.resize((target_width, target_height))
        resized_img.save(scaled_png_file)

    print(f"Rescaled PNG image saved as {scaled_png_file}")

    # Remove the temporary PNG file
    os.remove(temp_png_file)


def main():
    # Provide the URL of the page with the SVG
    url = 'https://www.maglaboratory.org/hal'

    # Hardcode the SVG ID
    svg_id = 'maglab-synoptic-view'

    # Scrape the SVG element from the website
    svg_content = scrape_svg(url, svg_id)

    if svg_content:
        # Save only the scaled PNG
        scaled_png_file = 'maglab_synoptic_view_scaled.png'
        save_scaled_png(svg_content, scaled_png_file)


if __name__ == '__main__':
    main()
