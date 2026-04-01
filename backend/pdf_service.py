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

            # Step 11: Horizontal Header/Date Layout: Move dates to the right side of headers
            # Common patterns: h3/h4 followed by a date/year p or span
            processed_nodes = set()
            for header in soup.find_all(['h3', 'h4', 'p']):
                if id(header) in processed_nodes: continue
                
                # Only process headers that have a sibling
                next_elem = header.find_next_sibling()
                if not next_elem: continue
                if id(next_elem) in processed_nodes: continue
                
                # Look for date-like text or organization name in the next sibling
                # Regex matches years (2024), "Present", months (Jan, May), etc., or certification prefixes (-- , | , —)
                # Word boundaries (\b) prevent matching "Mar" inside "Marketing"
                date_text = next_elem.get_text(strip=True)
                is_date = re.search(r'(\b(19|20)\d{2}\b|\bPresent\b|\bOngoing\b|\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b)', date_text, re.I)
                # Support em-dash (—), hyphen-space (- ), and double-hyphen (--)
                is_cert_org = date_text.startswith('--') or date_text.startswith('|') or date_text.startswith('—') or date_text.startswith('- ')
                
                if (is_date or is_cert_org) and len(date_text) < 50:
                    # Clear any existing styling
                    # Style the date/org to be bold and right-aligned
                    next_elem['style'] = 'margin: 0; padding: 0; font-weight: bold; text-align: right; font-size: 9.5px;'
                    
                    # Create a wrapper div using Flexbox for robust horizontal alignment
                    wrapper = soup.new_tag('div')
                    wrapper['style'] = 'display: flex; justify-content: space-between; align-items: baseline; width: 100%; margin-bottom: 1px; break-inside: avoid;'
                    
                    header.insert_before(wrapper)
                    # For Flexbox, the natural order (Header, then Date) works best
                    wrapper.append(header)
                    wrapper.append(next_elem)
                    
                    processed_nodes.add(id(header))
                    processed_nodes.add(id(next_elem))
                    
                    log_msg = f"Merged header '{header.get_text(strip=True)[:20]}...' with date '{date_text}' on same line"
                    logger.info(log_msg)

            # Step 12: Header Redesign: Center Name/Summary and Merge Contact Info
            h1 = soup.find('h1')
            if h1:
                h1['style'] = 'text-align: center; margin: 0 auto 1px auto; width: 100%;'
                
                # Center the summary/title paragraph immediately after name
                summary = h1.find_next(['p', 'div'])
                if summary:
                    summary['style'] = 'text-align: center; margin: 0 auto 3px auto; width: 100%; font-style: italic; font-size: 10.5px;'
                
                # Collect and merge contact info
                contact_parts = []
                # Scan next few elements for contact patterns
                curr = summary.find_next_sibling() if summary else h1.find_next_sibling()
                to_delete = []
                
                # Check up to 10 elements for contact info (Phone, Email, LinkedIn)
                for _ in range(10):
                    if not curr: break
                    text = curr.get_text(separator=' ', strip=True)
                    # Smart Contact Splitting: If multiple contact points are in a single element ("glued" together)
                    # or if they are separate elements, we find and extract them individually.
                    # This regex finds: phone numbers (10+ digits), email addresses, and URLs (starting with http or linkedin.com)
                    # This regex prioritizes URLs and uses boundaries to prevent greedy email matching
                    matches = re.findall(r'((?:https?://|www\.|linkedin\.com/)[^\s]+|[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,6}|\+?\d[\d\-\s]{8,})', text, re.I)
                    if matches:
                        # Clean each match and add to parts list
                        for m in matches:
                            clean_m = m.strip().strip(',').strip(';')
                            if clean_m and clean_m not in contact_parts:
                                contact_parts.append(clean_m)
                        to_delete.append(curr)
                    curr = curr.find_next_sibling()
                
                if contact_parts:
                    # Remove original contact elements
                    for node in to_delete:
                        node.decompose()
                    
                    # Create a single merged contact line
                    contact_line = soup.new_tag('div')
                    contact_line['style'] = 'text-align: center; margin: 0 auto 5px auto; width: 100%; font-size: 9.5px; font-weight: bold; white-space: pre-wrap; font-family: Courier, Courier New, monospace;'
                    
                    # Joining with WIDER spacing for ATS parsing and readability
                    # Joining with consistent spacing and a leading bullet for a professional appearance
                    separator = "    •    "
                    contact_line.string = "•    " + separator.join(contact_parts)
                    
                    if summary:
                        summary.insert_after(contact_line)
                    else:
                        h1.insert_after(contact_line)

            return str(soup)
        except Exception as e:
            logger.warning(f"Failed to preprocess HTML: {e}. Using original HTML.")
            return html_content

    def generate_pdf(self, html_content, jobId, title, resume_edit_url=None, company=None):
        """Generates a PDF from HTML content using WeasyPrint."""
        try:
            # Generate filename as title_company.pdf
            sanitized_title = self.sanitize_filename(title) if title else "Resume"
            sanitized_company = self.sanitize_filename(company) if company else "Company"
            filename = f"{sanitized_title}_{sanitized_company}.pdf"
            filepath = os.path.join(self.output_dir, filename)

            cleaned_html = self.remove_empty_photo_placeholders(html_content)

            full_html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
  @page {{ margin: 7mm 9mm; size: A4; }}
  * {{ box-sizing: border-box; max-width: 100%; word-wrap: break-word; overflow-wrap: break-word; }}
  body {{ font-family: Arial, Helvetica, sans-serif; font-size: 9.8px; color: #111; margin: 0; padding: 0; line-height: 1.22; }}
  div, section, header, article, span {{ display: block; width: 100%; height: auto; min-height: 0; padding: 0; margin: 0; }}
  h1 {{ font-size: 18px; font-weight: bold; margin: 0 auto 0.5px auto; letter-spacing: 0.5px; text-align: center; }}
  h2 {{ font-size: 12.5px; font-weight: bold; text-transform: uppercase; letter-spacing: 0.5px; border-bottom: 1.2px solid #111; margin: 4px 0 2px 0; padding-bottom: 1px; }}
  h3 {{ font-size: 10.5px; font-weight: bold; margin: 4px 0 1px 0; }}
  h4 {{ font-size: 9.8px; font-weight: bold; margin: 3px 0 1px 0; }}
  p {{ margin: 0.5px 0 1px 0; font-size: 9.8px; }}
  ul, ol {{ display: block; margin: 1px 0 2px 0; padding-left: 12px; }}
  ul {{ list-style-type: disc; }}
  ol {{ list-style-type: decimal; }}
  li {{ display: list-item; margin-bottom: 0.8px; font-size: 9.8px; line-height: 1.22; }}
  a {{ color: #111; text-decoration: none; }}
  article {{ margin-bottom: 2.5px; break-inside: avoid; }}
  section {{ margin-bottom: 1px; break-inside: avoid; }}
  /* Flex support for Header-Date alignment */
  .flex-container {{ display: flex; justify-content: space-between; align-items: baseline; width: 100%; }}
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