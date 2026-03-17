import json
import time
from datetime import datetime
import requests
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import configparser
import os
import sys
import re
import html
from urllib.parse import quote, quote_plus, urljoin


ACTIVE_BROWSER = None
SESSION_DATA = None


def main():
    global ACTIVE_BROWSER
    print(getNow(), "Initializing AutoRegister...")
    fileName = getFileName()
    config = initializeConfig(fileName)
    if config:
        browser = createBrowser(config)
        ACTIVE_BROWSER = browser
        login(browser, config['setup']['username'],
              config['setup']['password'])
        if not checkLogin(browser):
            term = getTerm(browser)
            enrolled = getEnrolledClass(browser)
            cartCourses = getCartCourses(browser)
            mode = config['class'].get('mode', 'cart').strip().lower()
            useDirectMode = mode == 'direct'

            times = getEnrolledTime(enrolled, term)
            credits = getTotalCredits(browser)
            courses = initializeCourses(config, enrolled)
            if useDirectMode and len(courses) > 0:
                writeCoursesToConfig(config, courses, fileName)
            timeBlacklist = generateTimeBlacklist(courses, times, term)
            restrictedBlacklist = initializeBlacklist(config, courses)
            writeBlacklistToConfig(config, restrictedBlacklist, fileName)
            goal = int(config['class']['goal'])
            if goal <= credits and useDirectMode and len(courses) > 0:
                goal = credits + 1
                print(f"Configured goal is not above current credits. Using temporary goal of {goal}.")
            if useDirectMode:
                print("Checking for availability for:")
                for course in courses:
                    print(course)
                if len(courses) == 0 and config['class']['class'].strip():
                    print("Falling back to cart-driven mode. Import the target section into your Registration Cart.")
            else:
                print("Using cart-driven mode. Put the target section in your Registration Cart.")
            print("Please do not manually drop or sign up classes. \n")
            last = []
            interval = 3600 * 9 # 3 hours
            nextRefresh = time.time() + interval
            cartPollSeconds = int(config['class'].get('cartPollSeconds', '60'))
            while credits < goal:
                if time.time() >= nextRefresh:
                    browser.refresh()
                    waitForRegistrationPage(browser)
                    print(getNow(), "Refreshing page.")
                    if checkLogin(browser):
                        login(browser, config['setup']['username'],
                              config['setup']['password'])
                        print("Logged out. Logging back in.")
                    nextRefresh = time.time() + interval
                if useDirectMode and len(courses) > 0:
                    openList = getClassStatus(
                        courses, term, timeBlacklist, restrictedBlacklist)
                    if len(openList) > 0 and last != openList:
                        addClass(browser, openList)
                        enrolled = getEnrolledClass(browser)
                        credits = getTotalCredits(browser)
                        deleteList = set()
                        for course in courses:
                            if course in enrolled:
                                print("Enrolled in", course)
                                deleteList.add(course)
                                writeCoursesToConfig(config, courses, fileName)
                                times = getEnrolledTime(enrolled, term)
                                timeBlacklist = generateTimeBlacklist(
                                    courses, times, term)
                        for delete in deleteList:
                            courses.remove(delete)
                        getMessage(browser, restrictedBlacklist, config)
                        writeBlacklistToConfig(
                            config, restrictedBlacklist, fileName)
                    last = openList
                    if len(courses) == 0:
                        break
                    time.sleep(5)
                    continue
                targetCartEntries = getTargetCartEntries(browser, config)
                if len(targetCartEntries) > 0:
                    openEntries = []
                    for entry in targetCartEntries:
                        if isCartEntryOpen(entry, term):
                            openEntries.append(entry)
                    if len(openEntries) == len(targetCartEntries):
                        print(getNow(), "All target cart sections are open. Attempting Update Schedule.")
                        submitCart(browser)
                        getMessage(browser, restrictedBlacklist, config)
                        enrolled = getEnrolledClass(browser)
                        credits = getTotalCredits(browser)
                        if hasTargetEnrolled(enrolled, config):
                            print("Enrolled in target cart course.")
                            break
                    else:
                        print(getNow(), f"Target course is in the Registration Cart, but only {len(openEntries)}/{len(targetCartEntries)} matching entries are currently open.")
                else:
                    print(getNow(), "Target course is not in the Registration Cart yet.")
                time.sleep(cartPollSeconds)
            if len(courses) == 0 and hasTargetEnrolled(enrolled, config):
                print('All classes added.')
            elif credits >= goal:
                print('Credit minimum reached.')
            elif hasTargetEnrolled(enrolled, config):
                print('Target course added from cart.')
        else:
            print("Login failed.")


def getFileName():
    if getattr(sys, 'frozen', False):
        path = os.path.dirname(sys.executable)
    elif __file__:
        path = os.path.dirname(__file__)
    return os.path.join(path, "config.ini")


def initializeConfig(fileName):
    config = configparser.ConfigParser()
    if not os.path.exists(fileName):
        config['setup'] = {
            'username': '',
            'password': '',
            'driverPath': '',
            'chromeUserDataDir': '',
            'chromeProfileDirectory': 'Default'
        }
        config['class'] = {
            'class': 'PSYCH 210',
            'mode': 'cart',
            'goal': '18',
            'blacklist': '',
            'cartPollSeconds': '60'
        }
        config.write(open(fileName, 'w'))
        print('Config file created at ', fileName)
        config.read(fileName)
        return config
    else:
        print("Config found at", fileName)
        config.read(fileName)
        changed = False
        if 'class' not in config:
            config['class'] = {}
            changed = True
        if 'mode' not in config['class']:
            config['class']['mode'] = 'cart'
            changed = True
        if changed:
            config.write(open(fileName, 'w'))
        return config


def login(browser, id, pw):
    browser.get("https://register.uw.edu/register/")
    print("Manual login required. Complete the UW login and land on the registration page in the browser window.")
    try:
        waitForRegistrationPage(browser, timeout=600)
    except Exception:
        print("Login timeout reached before the registration page loaded.")


def checkLogin(browser):
    return not isRegistrationPage(browser)


def getTerm(browser):
    waitForRegistrationPage(browser)
    title = browser.title.strip()
    if " - Register.UW" in title:
        return title.replace(" - Register.UW", "")
    elem = browser.find_element(By.ID, "reg-quarter")
    return elem.text.replace("Your ", "").replace(" Schedule", "")


def getEnrolledClass(browser):
    res = {}
    rows = browser.find_elements(By.CSS_SELECTOR, "#reg-current tbody tr")
    for row in rows:
        cells = row.find_elements(By.CSS_SELECTOR, "th, td")
        if len(cells) >= 2:
            split = cells[1].text.split()
            if len(split) >= 3:
                enrolledClass = split[0] + ' ' + split[1]
                section_code = split[2]
            else:
                continue
            if not enrolledClass in res:
                res[enrolledClass] = set()
            res[enrolledClass].add(section_code)
    return res


def getEnrolledTime(enrolled, term):
    res = []
    for course in enrolled:
        parser = getCourseJson(course)
        if parser:
            for n1 in parser.get('courseOfferingInstitutionList', []):
                for n2 in n1.get('courseOfferingTermList', []):
                    if n2.get('term') == term:
                        for section in n2.get('activityOfferingItemList', []):
                            if section.get('code') in enrolled[course]:
                                for meetingTime in section.get('meetingDetailsList', []):
                                    res.append(meetingTime)
    return res


def getTotalCredits(browser):
    text = browser.find_element(By.CSS_SELECTOR, "#reg-current .lead").text
    match = re.search(r"Registered Credits:\s*(\d+)", text)
    return int(match.group(1)) if match else 0


def addClass(browser, openList):
    if len(openList) == 0:
        return
    for registrationCode, courseName in openList.items():
        addToCart(browser, registrationCode)
        print(getNow(), "Attempting to enroll in", courseName)
    submit = WebDriverWait(browser, 30).until(
        EC.element_to_be_clickable((By.ID, "submit-registration"))
    )
    submit.click()
    time.sleep(2)


def getMessage(browser, restrictedBlacklist, config):
    messages = set()
    selectors = [
        "[role='alert']",
        ".alert",
        "#sln-suggest-messages",
        ".toast",
        ".invalid-feedback",
    ]
    for selector in selectors:
        for elem in browser.find_elements(By.CSS_SELECTOR, selector):
            text = elem.text.strip()
            if text:
                messages.add(text)
    for message in sorted(messages):
        print(message)


def getCourseName(parser):
    if parser:
        summary = parser.get('courseSummaryDetails', {})
        subject = summary.get('subjectArea')
        number = summary.get('courseNumber')
        if subject and number:
            return subject + ' ' + number


def generateTimeBlacklist(courses, times, term):
    res = set()
    for course in courses:
        parser = getCourseJson(course)
        if parser:
            for n1 in parser.get('courseOfferingInstitutionList', []):
                for n2 in n1.get('courseOfferingTermList', []):
                    if n2.get('term') == term:
                        for section in n2.get('activityOfferingItemList', []):
                            for meetingTime in section.get('meetingDetailsList', []):
                                if overlaps(meetingTime, times):
                                    code = section.get('code')
                                    if code:
                                        res.add(course + " " + code)
    return res


def overlaps(meetingTime, times):
    days1 = [a for a in re.split(r'([A-Z][a-z]*)', meetingTime['days']) if a]
    hours1 = meetingTime['time'].split(' - ')
    st1 = hours1[0]  # 1:30 PM
    et1 = hours1[1]
    for timee in times:
        days2 = [a for a in re.split(r'([A-Z][a-z]*)', timee['days']) if a]
        hours2 = timee['time'].split(' - ')
        st2 = hours2[0]
        et2 = hours2[1]
        if (isEarlier(st1, st2) and isEarlier(st2, et1)) or (isEarlier(st2, st1) and isEarlier(st1, et2)):
            for day in days1:
                if day in days2:
                    return True

# returns true if time1 is earlier than time2


def isEarlier(time1, time2):
    if time1[0:2] == "12":
        if 'AM' in time1:
            time1 = time1.replace('AM', 'PM')
        else:
            time1 = time1.replace('PM', 'AM')
    if time2[0:2] == "12":
        if 'AM' in time2:
            time2 = time2.replace('AM', 'PM')
        else:
            time2 = time2.replace('PM', 'AM')
    if time1[-2] != time2[-2]:
        if time1[-2] == 'A':
            return True
    else:
        split1 = time1.split(':')
        split2 = time2.split(':')
        hr1 = int(split1[0])
        hr2 = int(split2[0])
        min1 = int(split1[1][:2])
        min2 = int(split2[1][:2])
        if hr1 != hr2:
            if hr1 < hr2:
                return True
        else:
            if min1 <= min2:
                return True


def getClassStatus(courses, term, timeBlacklist, restrictedBlacklist):
    res = {}
    for course in courses:
        parser = getCourseJson(course)
        if parser:
            status = {}
            for n1 in parser.get('courseOfferingInstitutionList', []):
                for n2 in n1.get('courseOfferingTermList', []):
                    if n2.get('term') == term:
                        for section in n2.get('activityOfferingItemList', []):
                            code = section.get('code')
                            if not code:
                                continue
                            fullCourseName = course + ' ' + code
                            if not fullCourseName in timeBlacklist and not fullCourseName in restrictedBlacklist:
                                if len(code) == 1:
                                    if section.get('enrollStatus') == 'open' and section.get('addCodeRequired') == 'false':
                                        status[code] = {
                                            'registrationCode': section.get('registrationCode')
                                        }
                                else:
                                    if code[0] in status:
                                        sectionType = section.get('activityOfferingType')
                                        if not sectionType:
                                            continue
                                        if not sectionType in status[code[0]]:
                                            status[code[0]
                                                   ][sectionType] = {}
                                        if section.get('enrollStatus') == 'open' and section.get('addCodeRequired') == 'false':
                                            status[code[0]][sectionType][code] = {
                                                'registrationCode': section.get('registrationCode')
                                            }
            for lecture in status:
                canEnroll = True
                for sectionType in status[lecture]:
                    if sectionType != 'registrationCode' and len(status[lecture][sectionType]) == 0:
                        canEnroll = False
                if canEnroll:
                    res[status[lecture]['registrationCode']
                        ] = course + ' ' + lecture
                    for sectionType in status[lecture]:
                        if sectionType != 'registrationCode':
                            first = list(status[lecture]
                                         [sectionType].keys())[0]
                            res[status[lecture][sectionType][first]
                                ['registrationCode']] = course + ' ' + first
    return res


def initializeCourses(config, enrolled):
    courses = set(())
    for course in config['class']['class'].split(","):
        course = course.strip()
        page = getCourseJson(course)
        if page and getCourseName(page) and not getCourseName(page) in enrolled:
            courses.add(getCourseName(page))
    return courses


def initializeBlacklist(config, courses):
    res = set()
    for entry in config['class']['blacklist'].split(','):
        split = entry.strip().split(' ')
        if len(split) == 3 and split[0].upper() + ' ' + split[1].upper() in courses:
            res.add(entry)
    return res


def writeCoursesToConfig(config, courses, fileName):
    if len(courses) == 0:
        return
    res = ""
    if len(courses) > 0:
        courseList = list(courses)
        courseList.sort()
        for i in range(len(courseList) - 1):
            res += courseList[i] + ","
        res += courseList[-1]
    config['class']['class'] = res
    config.write(open(fileName, 'w'))


def writeBlacklistToConfig(config, blacklist, fileName):
    res = ""
    parsed = list(blacklist)
    parsed.sort()
    if len(parsed) > 0:
        for i in range(len(parsed) - 1):
            res += parsed[i] + ","
        res += parsed[-1]
    config['class']['blacklist'] = res
    config.write(open(fileName, 'w'))


def getCourseJson(course):
    response = fetchJson(getCourseLink(course))
    payload = normalizeCourseJson(response.get("json") if response else None)
    if response and response.get("ok") and payload:
        return payload
    status = response.get("status") if response else "unknown"
    print(getNow(), getCourseLink(course), f"error while attempting to load (status: {status})")
    if isinstance(response.get("json") if response else None, dict):
        keys = sorted(list((response.get("json") or {}).keys()))
        if len(keys) > 0:
            print(getNow(), getCourseLink(course), f"unexpected response keys: {', '.join(keys[:10])}")
            next_target = (response.get("json") or {}).get("next")
            if isinstance(next_target, str) and next_target.strip():
                print(getNow(), getCourseLink(course), f"response next target: {next_target}")
        else:
            print(getNow(), getCourseLink(course), "unexpected empty JSON object returned.")


def normalizeCourseJson(payload):
    if isCourseDetailsPayload(payload):
        return payload
    if not isinstance(payload, dict):
        return None
    for key in ("data", "course", "result", "payload"):
        nested = payload.get(key)
        if isCourseDetailsPayload(nested):
            return nested
    return None


def isCourseDetailsPayload(payload):
    return (
        isinstance(payload, dict) and
        isinstance(payload.get("courseSummaryDetails"), dict) and
        isinstance(payload.get("courseOfferingInstitutionList"), list)
    )


def getCourseLink(course):
    session = getSessionData()
    host = session.get("application", {}).get("myplan_hostname", "https://myplan.uw.edu")
    context = session.get("application", {}).get("myplan_context", "")
    base = host.rstrip("/")
    if context:
        base += "/" + context.strip("/")
    return base + "/course/api/courses/" + quote(course) + "/details"


def getMax(browser):
    return 0


def getNow():
    return datetime.fromtimestamp(time.time()).strftime('%Y-%m-%d %H:%M:%S')


def createBrowser(config):
    driverPath = config['setup'].get('driverPath', '').strip()
    chromeUserDataDir = config['setup'].get('chromeUserDataDir', '').strip()
    chromeProfileDirectory = config['setup'].get('chromeProfileDirectory', '').strip()
    if not chromeUserDataDir:
        localAppData = os.environ.get("LOCALAPPDATA", "").strip()
        if localAppData:
            defaultChromeDir = os.path.join(localAppData, "Google", "Chrome", "User Data")
            if os.path.isdir(defaultChromeDir):
                chromeUserDataDir = defaultChromeDir
    options = webdriver.ChromeOptions()
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    if chromeUserDataDir:
        options.add_argument(f"--user-data-dir={chromeUserDataDir}")
    if chromeProfileDirectory:
        options.add_argument(f"--profile-directory={chromeProfileDirectory}")
    if driverPath:
        return webdriver.Chrome(service=Service(driverPath), options=options)
    return webdriver.Chrome(options=options)


def needsManualLogin(id, pw):
    return True


def isRegistrationPage(browser):
    return (
        "register.uw.edu/register/" in browser.current_url and
        len(browser.find_elements(By.ID, "react_app")) > 0 and
        len(browser.find_elements(By.ID, "submit-registration")) > 0 and
        len(browser.find_elements(By.ID, "sln-suggest")) > 0
    )


def waitForRegistrationPage(browser, timeout=120):
    WebDriverWait(browser, timeout).until(lambda driver: isRegistrationPage(driver))


def addToCart(browser, registrationCode):
    input_elem = WebDriverWait(browser, 30).until(
        EC.element_to_be_clickable((By.ID, "sln-suggest"))
    )
    input_elem.clear()
    input_elem.send_keys(registrationCode)
    add_button = browser.find_element(By.CSS_SELECTOR, "#sln-suggest-form button[type='submit']")
    add_button.click()
    WebDriverWait(browser, 30).until(
        lambda driver: driver.find_element(By.ID, "sln-suggest").get_attribute("value") in ("", None)
    )


def getCartCourses(browser):
    courses = []
    rows = browser.find_elements(By.CSS_SELECTOR, "#reg-cart tbody tr")
    for row in rows:
        cells = row.find_elements(By.CSS_SELECTOR, "th, td")
        if len(cells) >= 2:
            sln = cells[0].text.strip()
            text = cells[1].text.strip()
            if text:
                courses.append({"sln": sln, "course": text})
    return courses


def hasTargetInCart(browser, config):
    targets = getConfiguredTargets(config)
    for entry in getCartCourses(browser):
        for target in targets:
            if entry["course"].upper().startswith(target.upper()):
                return True
    return False


def hasTargetEnrolled(enrolled, config):
    targets = getConfiguredTargets(config)
    for course in enrolled:
        for target in targets:
            if course.upper().startswith(target.upper()):
                return True
    return False


def getConfiguredTargets(config):
    return [course.strip() for course in config['class']['class'].split(",") if course.strip()]


def getTargetCartEntries(browser, config):
    targets = getConfiguredTargets(config)
    matches = []
    for entry in getCartCourses(browser):
        for target in targets:
            if entry["course"].upper().startswith(target.upper()):
                matches.append(entry)
                break
    return matches


def submitCart(browser):
    submit = WebDriverWait(browser, 30).until(
        EC.element_to_be_clickable((By.ID, "submit-registration"))
    )
    submit.click()
    time.sleep(2)


def getSessionData():
    global SESSION_DATA
    if SESSION_DATA is None:
        SESSION_DATA = browserFetchJson("api/session").get("json", {})
    return SESSION_DATA


def fetchJson(url):
    if not isinstance(url, str):
        return {}
    if not re.match(r"^https?://", url, re.IGNORECASE):
        return browserFetchJson(url)
    response = fetchJsonWithFetcher(url, requestsFetchJson)
    payload = normalizeCourseJson(response.get("json") if response else None)
    if response.get("ok") and payload:
        return response
    browser_response = fetchJsonWithFetcher(url, browserTabFetchJson)
    browser_payload = normalizeCourseJson(browser_response.get("json") if browser_response else None)
    if browser_response.get("ok") and browser_payload:
        return browser_response
    if browser_response.get("json") is not None:
        return browser_response
    return response


def fetchJsonWithFetcher(url, fetcher, max_hops=4):
    current_url = url
    last_response = {}
    for _ in range(max_hops):
        response = fetcher(current_url)
        if not isinstance(response, dict):
            return {}
        if response.get("final_url") is None:
            response["final_url"] = current_url
        last_response = response
        payload = normalizeCourseJson(response.get("json"))
        if payload:
            return response
        next_target = getJsonNextTarget(response.get("json"))
        if not next_target:
            return response
        current_url = urljoin(response.get("final_url") or current_url, next_target)
    return last_response


def getJsonNextTarget(payload):
    if not isinstance(payload, dict):
        return None
    next_target = payload.get("next")
    if isinstance(next_target, str) and next_target.strip():
        return next_target.strip()
    return None


def requestsFetchJson(url):
    cookies = getAllBrowserCookies()
    session = requests.Session()
    for cookie in cookies:
        name = cookie.get("name")
        value = cookie.get("value")
        if not name:
            continue
        session.cookies.set(
            name,
            value,
            domain=cookie.get("domain"),
            path=cookie.get("path", "/"),
        )
    try:
        response = session.get(
            url,
            timeout=30,
            allow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json, text/plain, */*",
            },
        )
    except Exception as error:
        return {"ok": False, "status": 0, "error": Stringify(error)}
    text = response.text
    try:
        payload = response.json()
    except Exception:
        payload = None
    return {
        "ok": response.ok,
        "status": response.status_code,
        "text": text,
        "json": payload,
        "final_url": response.url,
    }


def browserTabFetchJson(url):
    if ACTIVE_BROWSER is None:
        return {}
    original_handle = None
    new_handle = None
    try:
        original_handle = ACTIVE_BROWSER.current_window_handle
        handles_before = set(ACTIVE_BROWSER.window_handles)
        ACTIVE_BROWSER.execute_script("window.open(arguments[0], '_blank');", url)
        handles_after = set(ACTIVE_BROWSER.window_handles)
        new_handles = list(handles_after - handles_before)
        if len(new_handles) == 0:
            return {"ok": False, "status": 0}
        new_handle = new_handles[0]
        ACTIVE_BROWSER.switch_to.window(new_handle)
        WebDriverWait(ACTIVE_BROWSER, 30).until(
            lambda driver: driver.execute_script("return document.readyState") == "complete"
        )
        body_text = ACTIVE_BROWSER.find_element(By.TAG_NAME, "body").text.strip()
        page_source = ACTIVE_BROWSER.page_source
        payload = parseJsonText(body_text)
        if payload is None:
            payload = parseJsonText(page_source)
        return {
            "ok": payload is not None,
            "status": 200 if payload is not None else 0,
            "text": body_text,
            "json": payload,
            "final_url": ACTIVE_BROWSER.current_url,
        }
    except Exception as error:
        return {"ok": False, "status": 0, "error": Stringify(error)}
    finally:
        try:
            if new_handle and new_handle in ACTIVE_BROWSER.window_handles:
                ACTIVE_BROWSER.close()
        except Exception:
            pass
        try:
            if original_handle and original_handle in ACTIVE_BROWSER.window_handles:
                ACTIVE_BROWSER.switch_to.window(original_handle)
        except Exception:
            pass


def parseJsonText(text):
    if not text:
        return None
    text = text.strip()
    if text.startswith("<"):
        match = re.search(r"<pre[^>]*>(.*?)</pre>", text, re.IGNORECASE | re.DOTALL)
        if match:
            text = match.group(1).strip()
    try:
        payload = json.loads(text)
    except Exception:
        return None
    if isinstance(payload, dict):
        return payload
    return None


def getAllBrowserCookies():
    if ACTIVE_BROWSER is None:
        return []
    try:
        result = ACTIVE_BROWSER.execute_cdp_cmd("Network.getAllCookies", {})
        cookies = result.get("cookies", [])
        if isinstance(cookies, list):
            return cookies
    except Exception:
        pass
    try:
        cookies = ACTIVE_BROWSER.get_cookies()
        if isinstance(cookies, list):
            return cookies
    except Exception:
        pass
    return []


def Stringify(value):
    try:
        return str(value)
    except Exception:
        return "unknown error"


def browserFetchJson(url):
    if ACTIVE_BROWSER is None:
        return {}
    return ACTIVE_BROWSER.execute_async_script(
        """
        const url = arguments[0];
        const done = arguments[arguments.length - 1];
        fetch(url, { credentials: 'include' })
          .then(async (response) => {
            const text = await response.text();
            let json = null;
            try {
              json = JSON.parse(text);
            } catch (error) {}
            done({ ok: response.ok, status: response.status, text, json });
          })
          .catch((error) => done({ ok: false, status: 0, error: String(error) }));
        """,
        url,
    )


def isCartEntryOpen(entry, term):
    status = getRegistrationStatus(entry["course"], entry["sln"], term)
    if status is None:
        print(getNow(), f"Could not determine open status for {entry['course']} ({entry['sln']}).")
        return False
    print(getNow(), f"{entry['course']} ({entry['sln']}) live status: {status}")
    return status == "Open"


def getRegistrationStatus(courseText, sln, term):
    curriculum = getCurriculumFromCartText(courseText)
    url = getEnrollmentSummaryUrl(term, curriculum)
    print(getNow(), f"[debug] Checking enrollment summary for {courseText} ({sln}) via {url}")
    page = fetchEnrollmentSummaryPage(url)
    if not page:
        print(getNow(), f"[debug] No usable enrollment summary page returned for {url}")
        return None
    row = getEnrollmentSummaryRow(page, sln)
    if not row:
        print(getNow(), f"[debug] SLN {sln} not found in enrollment summary for {url}")
        return None
    print(
        getNow(),
        f"[debug] Summary row for SLN {sln}: course={row.get('course')!r}, "
        f"section={row.get('section')!r}, space_available={row.get('space_available')!r}, "
        f"notes={row.get('notes')!r}"
    )
    notes = row.get("notes", "")
    if "section withdrawn" in notes.lower():
        return "Withdrawn"
    if "entry code required" in notes.lower():
        return "Add Code Required"
    space = row.get("space_available")
    if space is None:
        return None
    if space > 0:
        return "Open"
    return "Closed"


def getCourseFromCartText(courseText):
    parts = courseText.split()
    if len(parts) >= 2:
        return parts[0] + ' ' + parts[1]
    return courseText.strip()


def getCurriculumFromCartText(courseText):
    parts = courseText.split()
    if len(parts) >= 1:
        return parts[0]
    return courseText.strip()


def getEnrollmentSummaryUrl(term, curriculum):
    qtryr = getEnrollmentSummaryTerm(term)
    return (
        "https://sdb.admin.uw.edu/timeschd/uwnetid/tsstat.asp?"
        f"QTRYR={quote_plus(qtryr)}&CURRIC={quote_plus(curriculum)}"
    )


def getEnrollmentSummaryTerm(term):
    parts = term.split()
    if len(parts) != 2:
        return term
    quarter_map = {
        "Winter": "WIN",
        "Spring": "SPR",
        "Summer": "SUM",
        "Autumn": "AUT",
    }
    return f"{quarter_map.get(parts[0], parts[0][:3].upper())} {parts[1]}"


def fetchEnrollmentSummaryPage(url):
    response = requestsFetchText(url)
    requests_ok = isEnrollmentSummaryHtml(response.get("text"))
    print(
        getNow(),
        f"[debug] Enrollment summary requests fetch: status={response.get('status')}, "
        f"final_url={response.get('final_url')}, html_ok={requests_ok}, error={response.get('error')}"
    )
    if requests_ok:
        return response.get("text")
    response = browserTabFetchText(url)
    browser_ok = isEnrollmentSummaryHtml(response.get("text"))
    print(
        getNow(),
        f"[debug] Enrollment summary browser fetch: status={response.get('status')}, "
        f"final_url={response.get('final_url')}, html_ok={browser_ok}, error={response.get('error')}"
    )
    if browser_ok:
        return response.get("text")
    return None


def requestsFetchText(url):
    cookies = getAllBrowserCookies()
    session = requests.Session()
    for cookie in cookies:
        name = cookie.get("name")
        value = cookie.get("value")
        if not name:
            continue
        session.cookies.set(
            name,
            value,
            domain=cookie.get("domain"),
            path=cookie.get("path", "/"),
        )
    try:
        response = session.get(
            url,
            timeout=30,
            allow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0"},
        )
    except Exception as error:
        return {"ok": False, "status": 0, "text": "", "error": Stringify(error)}
    return {
        "ok": response.ok,
        "status": response.status_code,
        "text": response.text,
        "final_url": response.url,
    }


def browserTabFetchText(url):
    if ACTIVE_BROWSER is None:
        return {}
    original_handle = None
    new_handle = None
    try:
        original_handle = ACTIVE_BROWSER.current_window_handle
        handles_before = set(ACTIVE_BROWSER.window_handles)
        ACTIVE_BROWSER.execute_script("window.open(arguments[0], '_blank');", url)
        handles_after = set(ACTIVE_BROWSER.window_handles)
        new_handles = list(handles_after - handles_before)
        if len(new_handles) == 0:
            return {"ok": False, "status": 0, "text": ""}
        new_handle = new_handles[0]
        ACTIVE_BROWSER.switch_to.window(new_handle)
        WebDriverWait(ACTIVE_BROWSER, 30).until(
            lambda driver: driver.execute_script("return document.readyState") == "complete"
        )
        return {
            "ok": True,
            "status": 200,
            "text": ACTIVE_BROWSER.page_source,
            "final_url": ACTIVE_BROWSER.current_url,
        }
    except Exception as error:
        return {"ok": False, "status": 0, "text": "", "error": Stringify(error)}
    finally:
        try:
            if new_handle and new_handle in ACTIVE_BROWSER.window_handles:
                ACTIVE_BROWSER.close()
        except Exception:
            pass
        try:
            if original_handle and original_handle in ACTIVE_BROWSER.window_handles:
                ACTIVE_BROWSER.switch_to.window(original_handle)
        except Exception:
            pass


def isEnrollmentSummaryHtml(text):
    if not text:
        return False
    return "Time Schedule Enrollment Summary" in text and "Space" in text and "Available" in text


def getEnrollmentSummaryRow(page, sln):
    row_html = None
    sln_text = str(sln).strip()
    for match in re.finditer(r"<tr\b[^>]*>.*?</tr>", page, re.IGNORECASE | re.DOTALL):
        candidate = match.group(0)
        if re.search(rf"SLN={re.escape(sln_text)}(?:\"|&|')", candidate, re.IGNORECASE):
            row_html = candidate
            break
    if not row_html:
        title_match = re.search(r"<title>(.*?)</title>", page, re.IGNORECASE | re.DOTALL)
        page_title = cleanHtmlCell(title_match.group(1)) if title_match else ""
        print(getNow(), f"[debug] Could not find SLN {sln} in page title={page_title!r}")
        return None
    cells = re.findall(r"<td\b[^>]*>(.*?)</td>", row_html, re.IGNORECASE | re.DOTALL)
    if len(cells) < 11:
        print(getNow(), f"[debug] SLN {sln} row had only {len(cells)} td cells")
        return None
    return {
        "course": cleanHtmlCell(cells[1]),
        "section": cleanHtmlCell(cells[2]),
        "space_available": parseSummaryNumber(cells[8]),
        "notes": cleanHtmlCell(cells[10]),
    }


def cleanHtmlCell(value):
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.IGNORECASE)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n+", "\n", value)
    return value.strip()


def parseSummaryNumber(value):
    text = cleanHtmlCell(value)
    match = re.search(r"\d+", text)
    if match:
        return int(match.group(0))
    return None


main()
