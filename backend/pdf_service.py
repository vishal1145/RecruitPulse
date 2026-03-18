import os
import logging
from weasyprint import HTML, CSS
import re
from bs4 import BeautifulSoup, NavigableString

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BULLET_CHARS = set('•◦▪▸●○■')

class PdfService:
    def __init__(self, output_dir="generated_pdfs"):
        self.output_dir = output_dir
        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir)
            logger.info(f"Created PDF output directory: {self.output_dir}")

    def sanitize_filename(self, filename):
        """Removes illegal characters from filename."""
        return re.sub(r'[^a-zA-Z0-9]', '_', filename)

    def remove_empty_photo_placeholders(self, html_content):
        """Cleans HTML for WeasyPrint: removes placeholders, embedded styles, Tailwind classes, fixes layout."""
        try:
            soup = BeautifulSoup(html_content, 'html.parser')

            # Step 1: Remove ALL embedded <style> tags (they contain width:794px etc)
            for style_tag in soup.find_all('style'):
                logger.info("Removing embedded <style> tag")
                style_tag.decompose()

            # Step 2: Remove empty img tags
            for img in soup.find_all('img'):
                src = img.get('src', '')
                if not src or src.strip() == '' or \
                src.startswith('data:image/svg+xml') or \
                'placeholder' in src.lower() or \
                'avatar' in src.lower() or \
                src == '#' or \
                src.startswith('data:,'):
                    img.decompose()

            # Step 3: Remove photo/avatar containers
            for elem in soup.find_all(['div', 'span', 'section']):
                classes = ' '.join(elem.get('class', [])).lower()
                if any(kw in classes for kw in ['photo', 'avatar', 'picture', 'profile-pic']):
                    has_valid_img = any(
                        img.get('src', '').strip() and not img.get('src', '').startswith('data:')
                        for img in elem.find_all('img')
                    )
                    if not has_valid_img:
                        elem.decompose()

            # Step 4: Remove ALL SVG elements
            for svg in soup.find_all('svg'):
                svg.decompose()

            # Step 5: Remove ALL empty containers
            for elem in soup.find_all(['div', 'section', 'header', 'span']):
                if not elem.get_text(strip=True) and not elem.find(['img', 'input', 'button', 'ul', 'ol', 'table']):
                    elem.decompose()

            # Step 6: Strip ALL class attributes (Tailwind classes mean nothing to WeasyPrint)
            for elem in soup.find_all(class_=True):
                del elem['class']

            # Step 7: Strip ALL inline style attributes (they contain fixed px widths)
            for elem in soup.find_all(style=True):
                del elem['style']

            # Step 8: Fix #resume-preview container
            preview = soup.find(id='resume-preview')
            if preview:
                preview['style'] = 'width: 100%; max-width: 100%; margin: 0; padding: 0;'

            # Step 9: Fix list items and strip embedded bullet chars
            for ul in soup.find_all('ul'):
                ul['style'] = 'display: block; margin: 4px 0 6px 0; padding-left: 16px; list-style-type: disc;'
            for ol in soup.find_all('ol'):
                ol['style'] = 'display: block; margin: 4px 0 6px 0; padding-left: 16px; list-style-type: decimal;'
            for li in soup.find_all('li'):
                li['style'] = 'display: list-item; list-style-type: disc; margin-bottom: 3px;'
                for child in list(li.children):
                    if hasattr(child, 'get_text') and child.get_text(strip=True) in BULLET_CHARS:
                        child.decompose()
                        break
                for child in list(li.children):
                    if isinstance(child, NavigableString):
                        cleaned = child.lstrip(''.join(BULLET_CHARS) + ' ')
                        if cleaned != str(child):
                            child.replace_with(cleaned)
                        break

            # Step 10: Remove standalone bullet-only elements
            for elem in soup.find_all(['span', 'div', 'p']):
                text = elem.get_text(strip=True)
                if text in BULLET_CHARS:
                    elem.decompose()

            return str(soup)
        except Exception as e:
            logger.warning(f"Failed to preprocess HTML: {e}. Using original HTML.")
            return html_content

    def generate_pdf(self, html_content, jobId, title, resume_edit_url=None):
        """Generates a PDF from HTML content using WeasyPrint."""
        try:
            sanitized_job_id = self.sanitize_filename(jobId)
            filename = f"RecruitPulse_{sanitized_job_id}.pdf"
            filepath = os.path.join(self.output_dir, filename)

            cleaned_html = self.remove_empty_photo_placeholders(html_content)

            full_html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
  @page {{ margin: 12mm 12mm; size: A4; }}

  * {{ box-sizing: border-box; max-width: 100%; word-wrap: break-word; overflow-wrap: break-word; }}

  body {{ font-family: Arial, Helvetica, sans-serif; font-size: 11px; color: #111; margin: 0; padding: 0; line-height: 1.5; }}

  div, section, header, article, span {{ display: block; width: 100%; height: auto; min-height: 0; padding: 0; margin: 0; }}

  h1 {{ font-size: 22px; font-weight: bold; margin: 0 0 5px 0; letter-spacing: 0.5px; }}
  h2 {{ font-size: 13px; font-weight: bold; text-transform: uppercase; letter-spacing: 1px; border-bottom: 1.5px solid #111; margin: 14px 0 6px 0; padding-bottom: 2px; }}
  h3 {{ font-size: 11.5px; font-weight: bold; margin: 9px 0 2px 0; }}
  h4 {{ font-size: 11px; font-weight: bold; margin: 7px 0 2px 0; }}

  p {{ margin: 2px 0 3px 0; font-size: 11px; }}

  ul, ol {{ display: block; margin: 3px 0 6px 0; padding-left: 18px; }}
  ul {{ list-style-type: disc; }}
  ol {{ list-style-type: decimal; }}
  li {{ display: list-item; margin-bottom: 3px; font-size: 11px; line-height: 1.5; }}

  a {{ color: #111; text-decoration: none; }}

  article {{ margin-bottom: 8px; }}
  section {{ margin-bottom: 6px; }}
</style>
</head>
<body>
{cleaned_html}
</body>
</html>"""

            HTML(string=full_html).write_pdf(filepath)

            logger.info(f"Successfully generated PDF: {filepath}")
            return filename
        except Exception as e:
            logger.error(f"Failed to generate PDF: {e}")
            return None