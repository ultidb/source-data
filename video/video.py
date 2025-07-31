import vimeo, json, requests, os, re, time
from bs4 import BeautifulSoup, NavigableString, Tag, element
from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

load_dotenv(override=True)
API_URL = os.getenv("API_URL")
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
VIMEO_CLIENT_ID = os.getenv("VIMEO_CLIENT_ID")
VIMEO_CLIENT_SECRET = os.getenv("VIMEO_CLIENT_SECRET")
VIMEO_ACCESS_TOKEN = os.getenv("VIMEO_ACCESS_TOKEN")


class Video:
    def __init__(self, title, publishedAt, url, tournamentName="", division=""):
        self.title = title.replace(",", "")
        self.publishedAt = publishedAt
        self.url = url
        self.tournamentName = tournamentName
        self.division = division

    def to_csv(self):
        o = self.title.replace('"', "")
        tn = ""
        if self.tournamentName != "":
            tn = "," + self.tournamentName.replace(",", "").strip()
        div = ""
        if self.division != "":
            div = "," + self.division.replace("'", "").strip()
        return f"{o}{tn}{div},{self.publishedAt},{self.url}"


def getActiveVideoScrapers():
    url = f"{API_URL}/v1/videos/sources/active"
    response = requests.get(url)
    if response.status_code != 200:
        print(f"Error: received status code {response.status_code}")
        return
    data = response.json()

    if data is None or len(data) == 0:
        print("No active video scrapers found")
        return []
    else:
        print(f"Found {len(data)} active video scrapers")
        return data


def scrapeVideos():
    sources = getActiveVideoScrapers()
    for source in sources:
        results = []
        csvHeaderLine = "title,published_at,url\n"
        print(f"scraping {source['platform']}, {source['name']}")
        if source["platform"].lower() == "vimeo":
            results = scrapeVimeoUser(source["channelID"])
        elif source["platform"].lower() == "youtube":
            results = scrapeYoutubeChannel(source["channelID"])
        elif source["platform"].lower() == "ultiworld":
            csvHeaderLine = "title,tournament,division,published_at,url\n"
            results = scrapeUltiworld()
        else:
            print(
                f'Unknown video source platform for {source["source"]}: {source["platform"]}'
            )
        if len(results):
            filename = f'video/csv/{source["id"]}.csv'
            with open(filename, "w") as f:
                f.write(csvHeaderLine)
                for result in results:
                    f.write(f"{result.to_csv()}\n")


def scrapeVimeoUser(userId):
    client = vimeo.VimeoClient(
        token=f"{VIMEO_ACCESS_TOKEN}",
        key=f"{VIMEO_CLIENT_ID}",
        secret=f"{VIMEO_CLIENT_SECRET}",
    )
    response = client.get(f"/users/{userId}/videos?filter=playable&per_page=100")
    if response.status_code != 200:
        print(f"Error: received status code {response.status_code}")
        return []

    data = response.json()

    while data["paging"]["next"] is not None:
        print(f'fetching next page {data["paging"]["next"]}')
        response = client.get(data["paging"]["next"])
        tmp = response.json()
        data["data"].extend(tmp["data"])
        data["paging"] = tmp["paging"]
        print(f'fetched {len(data["data"])} videos')
    output = []
    for video in data["data"]:
        output.append(Video(video["name"], video["created_time"], video["link"]))
    return output


def scrapeYoutubeChannel(channel_id):
    base_url = "https://www.googleapis.com/youtube/v3"
    playlist_id = ""
    videos = []

    # Get the uploads playlist ID for the channel
    response = requests.get(
        f"{base_url}/channels?part=contentDetails&id={channel_id}&key={YOUTUBE_API_KEY}"
    )
    data = response.json()
    if "items" in data and len(data["items"]) > 0:
        playlist_id = data["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
    else:
        print("Couldn't fetch the playlist ID for the channel.")
        return []

    # Fetch video titles from the uploads playlist
    next_page_token = ""
    while True:
        response = requests.get(
            f"{base_url}/playlistItems?part=snippet&maxResults=50&playlistId={playlist_id}&pageToken={next_page_token}&key={YOUTUBE_API_KEY}"
        )
        data = response.json()
        for item in data["items"]:
            videos.append(item["snippet"])

        if "nextPageToken" in data:
            next_page_token = data["nextPageToken"]
        else:
            break

    output = []
    for item in videos:
        output.append(
            Video(
                item["title"],
                item["publishedAt"],
                f'https://www.youtube.com/watch?v={item["resourceId"]["videoId"]}',
            )
        )

    return output

def scrapeUltiworldAndSave():
    results = scrapeUltiworld()
    csvHeaderLine = "title,tournament,division,published_at,url\n"
    with open('video/csv/6.csv', "w") as f:
        f.write(csvHeaderLine)
        for result in results:
            f.write(f"{result.to_csv()}\n")


def scrapeUltiworld():
    urls = [
        # "https://ultiworld.com/video/?years=2014&divisions=&packages=&event=&tags=#filtered",
        # "https://ultiworld.com/video/?years=2015&divisions=&packages=&event=&tags=#filtered",
        # "https://ultiworld.com/video/?years=2016&divisions=&packages=&event=&tags=#filtered",
        # "https://ultiworld.com/video/?years=2017&divisions=&packages=&event=&tags=#filtered",
        # "https://ultiworld.com/video/?years=2018&divisions=usau-club&packages=&event=&tags=#filtered",
        # "https://ultiworld.com/video/?years=2018&divisions=usau-college&packages=&event=&tags=#filtered",
        # "https://ultiworld.com/video/?years=2018&divisions=usau-youth-club&packages=&event=&tags=#filtered",
        # "https://ultiworld.com/video/?years=2019&divisions=usau-club&packages=&event=&tags=#filtered",
        # "https://ultiworld.com/video/?years=2019&divisions=usau-college&packages=&event=&tags=#filtered",
        # "https://ultiworld.com/video/?years=2019&divisions=usau-youth-club&packages=&event=&tags=#filtered",
        # "https://ultiworld.com/video/?years=2020&divisions=usau-college&packages=&event=&tags=#filtered",
        # "https://ultiworld.com/video/?years=2021&divisions=usau-college&packages=&event=&tags=#filtered",
        # "https://ultiworld.com/video/?years=2021&divisions=usau-club&packages=&event=&tags=#filtered",
        # "https://ultiworld.com/video/?years=2021&divisions=usau-youth-club&packages=&event=&tags=#filtered",
        # "https://ultiworld.com/video/?years=2022&divisions=usau-club&packages=&event=&tags=#filtered",
        # "https://ultiworld.com/video/?years=2022&divisions=usau-college&packages=&event=&tags=#filtered",
        # "https://ultiworld.com/video/?years=2022&divisions=usau-youth-club&packages=&event=&tags=#filtered",
        # "https://ultiworld.com/video/?years=2023&divisions=usau-college-d-i-mens&packages=&event=&tags=#filtered",
        # "https://ultiworld.com/video/?years=2023&divisions=usau-college-d-i-womens&packages=&event=&tags=#filtered",
        # "https://ultiworld.com/video/?years=2023&divisions=usau-college-d-iii-mens&packages=&event=&tags=#filtered",
        # "https://ultiworld.com/video/?years=2023&divisions=usau-college-d-iii-womens&packages=&event=&tags=#filtered",
        # "https://ultiworld.com/video/?years=2023&divisions=usau-club-mens&packages=&event=&tags=#filtered",
        # "https://ultiworld.com/video/?years=2023&divisions=usau-club-mixed&packages=&event=&tags=#filtered",
        # "https://ultiworld.com/video/?years=2023&divisions=usau-club-womens&packages=&event=&tags=#filtered",
        # "https://ultiworld.com/video/?years=2023&divisions=usau-youth-club&packages=&event=&tags=#filtered",
        # "https://ultiworld.com/video/?years=2024&divisions=usau-college-d-i-mens&packages=&event=&tags=#filtered",
        # "https://ultiworld.com/video/?years=2024&divisions=usau-college-d-i-womens&packages=&event=&tags=#filtered",
        # "https://ultiworld.com/video/?years=2024&divisions=usau-college-d-iii-mens&packages=&event=&tags=#filtered",
        # "https://ultiworld.com/video/?years=2024&divisions=usau-college-d-iii-womens&packages=&event=&tags=#filtered",
        # "https://ultiworld.com/video/?years=2024&divisions=usau-club-mens&packages=&event=&tags=#filtered",
        # "https://ultiworld.com/video/?years=2024&divisions=usau-club-mixed&packages=&event=&tags=#filtered",
        # "https://ultiworld.com/video/?years=2024&divisions=usau-club-womens&packages=&event=&tags=#filtered",
        "https://ultiworld.com/video/?years=2025&divisions=usau-college-d-i-mens&packages=&event=&tags=#filtered",
        "https://ultiworld.com/video/?years=2025&divisions=usau-college-d-i-womens&packages=&event=&tags=#filtered",
        "https://ultiworld.com/video/?years=2025&divisions=usau-college-d-iii-mens&packages=&event=&tags=#filtered",
        "https://ultiworld.com/video/?years=2025&divisions=usau-college-d-iii-womens&packages=&event=&tags=#filtered",
        "https://ultiworld.com/video/?years=2025&divisions=usau-club-mens&packages=&event=&tags=#filtered",
        "https://ultiworld.com/video/?years=2025&divisions=usau-club-mixed&packages=&event=&tags=#filtered",
        "https://ultiworld.com/video/?years=2025&divisions=usau-club-womens&packages=&event=&tags=#filtered",
    ]

    chrome_options = Options()
    # Keep non-headless mode to avoid Cloudflare challenges
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument("--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option('useAutomationExtension', False)
    
    driver = webdriver.Chrome(options=chrome_options)
    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

    urlPattern = r"(https://ultiworld\.com/video/\d+/).*"
    pattern = r"years=(\d{4})"
    years = {
        "2014": 0,
        "2015": 0,
        "2016": 0,
        "2017": 0,
        "2018": 0,
        "2019": 0,
        "2020": 0,
        "2021": 0,
        "2022": 0,
        "2023": 0,
        "2024": 0,
        "2025": 0,
    }
    output = []
    
    try:
        for url in urls:
            match = re.search(pattern, url)
            year = None
            if match:
                year = match.group(1)

            print(f"Scraping {url}")
            driver.get(url)
            
            # Wait for content to load
            time.sleep(5)
            
            # Try different ways to detect loaded content
            selectors_to_try = [
                "div.video-grid__item",
                ".video-grid__item", 
                "[class*='video-grid']",
                "[class*='video']"
            ]
            
            items = []
            for selector in selectors_to_try:
                try:
                    WebDriverWait(driver, 15).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, selector))
                    )
                    soup = BeautifulSoup(driver.page_source, "html.parser")
                    items = soup.select(selector)
                    if items:
                        break
                except Exception as e:
                    continue
            
            if not items:
                print(f"No video items found for {url}")
                continue

            if year:
                years[year] += len(items)

            # Parse items that match video-grid structure
            for item in items:
                title_elem = item.find("span", class_="video-grid__item-title")
                if not title_elem:
                    continue
                    
                title = title_elem.text.strip()
                subtitle = item.find("span", class_="video-grid__item-subtitle")
                tournamentName = ""
                division = ""

                if subtitle:
                    links = subtitle.find_all("a")
                    if len(links) == 2:
                        tournamentName = links[0].text.strip()
                        division = links[1].text.strip()

                link_elem = item.find("a")
                if not link_elem or not link_elem.get("href"):
                    continue
                    
                video_url = link_elem["href"]
                if not video_url.startswith("http"):
                    video_url = "https://ultiworld.com" + video_url
                    
                match = re.match(urlPattern, video_url)
                if match:
                    video_url = match.group(1)
                elif "/video/" in video_url:
                    pass
                else:
                    continue

                if tournamentName == "2023 Club National Championships":
                    tournamentName = "2023 USA Ultimate Club Championships"

                output.append(Video(title, "", video_url, tournamentName, division))
            
            # Small delay between requests
            time.sleep(2)
            
    finally:
        driver.quit()
    
    print(f"Scraped {len(output)} videos total")
    for year, count in years.items():
        if count > 0:
            print(f"{year}: {count} videos")
    
    return output
