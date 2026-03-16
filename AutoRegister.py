import requests
import json
import time
from datetime import datetime
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
from urllib.parse import quote


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

            times = getEnrolledTime(enrolled, term)
            credits = getTotalCredits(browser)
            courses = initializeCourses(config, enrolled)
            if len(courses) > 0:
                writeCoursesToConfig(config, courses, fileName)
            timeBlacklist = generateTimeBlacklist(courses, times, term)
            restrictedBlacklist = initializeBlacklist(config, courses)
            writeBlacklistToConfig(config, restrictedBlacklist, fileName)
            goal = int(config['class']['goal'])
            if goal <= credits and len(courses) > 0:
                goal = credits + 1
                print(f"Configured goal is not above current credits. Using temporary goal of {goal}.")
            print("Checking for availability for:")
            for course in courses:
                print(course)
            if len(courses) == 0 and config['class']['class'].strip():
                print("Falling back to cart-driven mode. Import the target section into your Registration Cart.")
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
                if len(courses) > 0:
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
                    if len(openEntries) > 0:
                        print(getNow(), "Target cart section is open. Attempting Update Schedule.")
                        submitCart(browser)
                        getMessage(browser, restrictedBlacklist, config)
                        enrolled = getEnrolledClass(browser)
                        credits = getTotalCredits(browser)
                        if hasTargetEnrolled(enrolled, config):
                            print("Enrolled in target cart course.")
                            break
                    else:
                        print(getNow(), "Target course is in the Registration Cart, but the section is still closed.")
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
            for n1 in parser['courseOfferingInstitutionList']:
                for n2 in n1['courseOfferingTermList']:
                    if n2['term'] == term:
                        for section in n2['activityOfferingItemList']:
                            if section['code'] in enrolled[course]:
                                for meetingTime in section['meetingDetailsList']:
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
        return parser['courseSummaryDetails']['subjectArea'] + ' ' + parser['courseSummaryDetails']['courseNumber']


def generateTimeBlacklist(courses, times, term):
    res = set()
    for course in courses:
        parser = getCourseJson(course)
        if parser:
            for n1 in parser['courseOfferingInstitutionList']:
                for n2 in n1['courseOfferingTermList']:
                    if n2['term'] == term:
                        for section in n2['activityOfferingItemList']:
                            for meetingTime in section['meetingDetailsList']:
                                if overlaps(meetingTime, times):
                                    res.add(course + " " + section['code'])
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
            for n1 in parser['courseOfferingInstitutionList']:
                for n2 in n1['courseOfferingTermList']:
                    if n2['term'] == term:
                        for section in n2['activityOfferingItemList']:
                            fullCourseName = course + ' ' + section['code']
                            if not fullCourseName in timeBlacklist and not fullCourseName in restrictedBlacklist:
                                if len(section['code']) == 1:
                                    if section['enrollStatus'] == 'open' and section['addCodeRequired'] == 'false':
                                        status[section['code']] = {
                                            'registrationCode': section['registrationCode']
                                        }
                                else:
                                    if section['code'][0] in status:
                                        sectionType = section['activityOfferingType']
                                        if not sectionType in status[section['code'][0]]:
                                            status[section['code'][0]
                                                   ][sectionType] = {}
                                        if section['enrollStatus'] == 'open' and section['addCodeRequired'] == 'false':
                                            status[section['code'][0]][sectionType][section['code']] = {
                                                'registrationCode': section['registrationCode']
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
    response = browserFetchJson(getCourseLink(course))
    if response and response.get("ok") and isinstance(response.get("json"), dict):
        return response["json"]
    status = response.get("status") if response else "unknown"
    print(getNow(), getCourseLink(course), f"error while attempting to load (status: {status})")


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
    status = getPublicTimeScheduleStatus(entry["course"], entry["sln"], term)
    if status is None:
        print(getNow(), f"Could not determine open status for {entry['course']} ({entry['sln']}).")
        return False
    print(getNow(), f"{entry['course']} ({entry['sln']}) public status: {status}")
    return status == "Open"


def getPublicTimeScheduleStatus(courseText, sln, term):
    subject = courseText.split()[0].lower()
    termCode = getTimeScheduleTermCode(term)
    url = f"https://www.washington.edu/students/timeschd/pub/{termCode}/{subject}.html"
    try:
        response = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    except Exception:
        return None
    if response.status_code != 200:
        return None
    text = html.unescape(response.text)
    flattened = re.sub(r"<[^>]+>", " ", text)
    flattened = re.sub(r"\s+", " ", flattened)
    match = re.search(rf"\b{re.escape(sln)}\b.{{0,120}}?\b(Open|Closed)\b", flattened, re.IGNORECASE)
    if match:
        return match.group(1).title()
    return None


def getTimeScheduleTermCode(term):
    parts = term.split()
    if len(parts) != 2:
        return term
    quarterMap = {
        "Winter": "WIN",
        "Spring": "SPR",
        "Summer": "SUM",
        "Autumn": "AUT",
    }
    return quarterMap.get(parts[0], parts[0][:3].upper()) + parts[1]


main()
