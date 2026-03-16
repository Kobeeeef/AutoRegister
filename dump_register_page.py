from pathlib import Path
import sys
import time

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait


REGISTER_URL = "https://register.uw.edu/register/"


def main():
    output_dir = Path(__file__).resolve().parent / "debug"
    output_dir.mkdir(exist_ok=True)

    options = webdriver.ChromeOptions()
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    browser = webdriver.Chrome(options=options)
    browser.get(REGISTER_URL)
    print("Chrome opened.")
    print("Log in manually and navigate to the UW registration page you want to automate.")
    print("The script will save the HTML automatically once it detects the registration UI.")
    WebDriverWait(browser, 900).until(lambda driver: is_registration_ui_ready(driver))
    time.sleep(3)

    html_path = output_dir / "register_page.html"
    html_path.write_text(browser.page_source, encoding="utf-8")
    print(f"Saved HTML to {html_path}")
    print(f"Current URL: {browser.current_url}")

    screenshot_path = output_dir / "register_page.png"
    browser.save_screenshot(str(screenshot_path))
    print(f"Saved screenshot to {screenshot_path}")

    browser.quit()


def is_register_app(browser):
    return (
        "register.uw.edu" in browser.current_url and
        len(browser.find_elements(By.TAG_NAME, "body")) > 0
    )


def is_registration_ui_ready(browser):
    if not is_register_app(browser):
        return False
    body = browser.find_element(By.TAG_NAME, "body").text
    markers = [
        "Registration Cart",
        "Update Schedule",
        "Import from MyPlan",
        "Enter SLN",
    ]
    return any(marker in body for marker in markers)


if __name__ == "__main__":
    main()
