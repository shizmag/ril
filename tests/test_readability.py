from ril import readability_utils

def test_extract_article_success():
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Primary Title of the Page</title>
    </head>
    <body>
        <nav>
            <ul>
                <li>Home</li>
                <li>About</li>
            </ul>
        </nav>
        <article>
            <h1>My Custom Article Title</h1>
            <p>This is the core content of the article. It has enough words and text to be parsed 
            properly by the Readability engine as the main body. Readability looks for paragraphs 
            with text blocks to identify the main readability context.</p>
            <p>Secondary paragraph with more interesting details about Python and Playwright scraping.</p>
        </article>
        <footer>
            <p>Copyright 2026. All rights reserved.</p>
        </footer>
    </body>
    </html>
    """
    
    title, clean_html = readability_utils.extract_article(html)
    
    assert "My Custom Article Title" in title or "Primary Title" in title
    assert "This is the core content of the article" in clean_html
    # Header/Footer/Nav elements are normally filtered out of summary
    assert "Home" not in clean_html
    assert "Copyright" not in clean_html

def test_extract_article_fail_fallback():
    # If Document throws an error (e.g. None or invalid inputs), it should fallback gracefully
    title, clean_html = readability_utils.extract_article(None)
    assert title == "Extraction Failed"
    assert clean_html is None
