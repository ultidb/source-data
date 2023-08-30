import vimeo, json, requests, os, re
from bs4 import BeautifulSoup, NavigableString, Tag, element
from dotenv import load_dotenv

load_dotenv(override=True)
API_URL = os.getenv("API_URL")
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
VIMEO_CLIENT_ID = os.getenv("VIMEO_CLIENT_ID")
VIMEO_CLIENT_SECRET = os.getenv("VIMEO_CLIENT_SECRET")
VIMEO_ACCESS_TOKEN = os.getenv("VIMEO_ACCESS_TOKEN")

class Video:
    def __init__(self, title, publishedAt, url, tournamentName="", division=""):
        self.title = title.replace(',', '')
        self.publishedAt = publishedAt
        self.url = url
        self.tournamentName = tournamentName
        self.division = division
    def to_csv(self):
        o = self.title.replace('"', '')
        tn = ""
        if self.tournamentName != "":
            tn = ',' + self.tournamentName.replace(',', '').strip()
        div = ""
        if self.division != "":
            div = ',' + self.division.replace("'", "").strip()
        return f'{o}{tn}{div},{self.publishedAt},{self.url}'

def getActiveVideoScrapers():
    url = f'{API_URL}/v1/videos/sources/active'
    response = requests.get(url)
    if response.status_code != 200:
        print(f'Error: received status code {response.status_code}')
        return
    data = response.json()

    if data is None or len(data) == 0:
        print('No active video scrapers found')
        return []
    else:
        print(f'Found {len(data)} active video scrapers')   
        return data

def scrapeVideos():
    sources = getActiveVideoScrapers()
    for source in sources:
        results = []
        csvHeaderLine = 'title,published_at,url\n'
        if source['platform'].lower() == 'vimeo':
            results = scrapeVimeoUser(source['channelID'])
        elif source['platform'].lower() == 'youtube':
            results = scrapeYoutubeChannel(source['channelID'], YOUTUBE_API_KEY)
        elif source['platform'].lower() == 'ultiworld':
            csvHeaderLine = 'title,tournament,division,published_at,url\n'
            results = scrapeUltiworld()
        else:
            print(f'Unknown video source platform for {source["source"]}: {source["platform"]}')
        if len(results):
            filename = f'video/csv/{source["id"]}.csv'
            with open(filename, 'w') as f:
                f.write(csvHeaderLine)
                for result in results:
                    f.write(f'{result.to_csv()}\n')


def scrapeVimeoUser(userId):
    client = vimeo.VimeoClient(
        token=f'{VIMEO_ACCESS_TOKEN}',
        key=f'{VIMEO_CLIENT_ID}',
        secret=f'{VIMEO_CLIENT_SECRET}'
    )
    response = client.get(f'/users/{userId}/videos?filter=playable&per_page=100')
    if response.status_code != 200:
        print(f'Error: received status code {response.status_code}')
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

def scrapeYoutubeChannel(channel_id, api_key):
    base_url = 'https://www.googleapis.com/youtube/v3'
    playlist_id = ''
    videos = []

    # Get the uploads playlist ID for the channel
    response = requests.get(f'{base_url}/channels?part=contentDetails&id={channel_id}&key={api_key}')
    data = response.json()
    if 'items' in data and len(data['items']) > 0:
        playlist_id = data['items'][0]['contentDetails']['relatedPlaylists']['uploads']
    else:
        print("Couldn't fetch the playlist ID for the channel.")
        return []

    # Fetch video titles from the uploads playlist
    next_page_token = ''
    while True:
        response = requests.get(f'{base_url}/playlistItems?part=snippet&maxResults=50&playlistId={playlist_id}&pageToken={next_page_token}&key={api_key}')
        data = response.json()
        for item in data['items']:
            videos.append(item['snippet'])

        if 'nextPageToken' in data:
            next_page_token = data['nextPageToken']
        else:
            break

    output = []
    for item in videos:
        output.append(
            Video(
                item['title'],
                item['publishedAt'],
                f'https://www.youtube.com/watch?v={item["resourceId"]["videoId"]}'
            ))

    return output

def scrapeUltiworld():
    urls = [
        'https://ultiworld.com/video/?years=2014&divisions=&packages=&event=&tags=#filtered',
        'https://ultiworld.com/video/?years=2015&divisions=&packages=&event=&tags=#filtered',
        'https://ultiworld.com/video/?years=2016&divisions=&packages=&event=&tags=#filtered',
        'https://ultiworld.com/video/?years=2017&divisions=&packages=&event=&tags=#filtered',
        'https://ultiworld.com/video/?years=2018&divisions=usau-club&packages=&event=&tags=#filtered',
        'https://ultiworld.com/video/?years=2018&divisions=usau-college&packages=&event=&tags=#filtered',
        'https://ultiworld.com/video/?years=2018&divisions=usau-youth-club&packages=&event=&tags=#filtered',
        'https://ultiworld.com/video/?years=2019&divisions=usau-club&packages=&event=&tags=#filtered',
        'https://ultiworld.com/video/?years=2019&divisions=usau-college&packages=&event=&tags=#filtered',
        'https://ultiworld.com/video/?years=2019&divisions=usau-youth-club&packages=&event=&tags=#filtered',
        'https://ultiworld.com/video/?years=2020&divisions=usau-college&packages=&event=&tags=#filtered',
        'https://ultiworld.com/video/?years=2021&divisions=usau-college&packages=&event=&tags=#filtered',
        'https://ultiworld.com/video/?years=2021&divisions=usau-club&packages=&event=&tags=#filtered',
        'https://ultiworld.com/video/?years=2021&divisions=usau-youth-club&packages=&event=&tags=#filtered',
        'https://ultiworld.com/video/?years=2022&divisions=usau-club&packages=&event=&tags=#filtered',
        'https://ultiworld.com/video/?years=2022&divisions=usau-college&packages=&event=&tags=#filtered',
        'https://ultiworld.com/video/?years=2022&divisions=usau-youth-club&packages=&event=&tags=#filtered',
        'https://ultiworld.com/video/?years=2023&divisions=usau-college-d-i-mens&packages=&event=&tags=#filtered',
        'https://ultiworld.com/video/?years=2023&divisions=usau-college-d-i-womens&packages=&event=&tags=#filtered',
        'https://ultiworld.com/video/?years=2023&divisions=usau-college-d-iii-mens&packages=&event=&tags=#filtered',
        'https://ultiworld.com/video/?years=2023&divisions=usau-college-d-iii-womens&packages=&event=&tags=#filtered',
        'https://ultiworld.com/video/?years=2023&divisions=usau-club-mens&packages=&event=&tags=#filtered',
        'https://ultiworld.com/video/?years=2023&divisions=usau-club-mixed&packages=&event=&tags=#filtered',
        'https://ultiworld.com/video/?years=2023&divisions=usau-club-womens&packages=&event=&tags=#filtered',
        'https://ultiworld.com/video/?years=2023&divisions=usau-youth-club&packages=&event=&tags=#filtered',
    ]

    years = {
        '2014': 0,
        '2015': 0,
        '2016': 0,
        '2017': 0,
        '2018': 0,
        '2019': 0,
        '2020': 0,
        '2021': 0,
        '2022': 0,
        '2023': 0,
    }
    pattern = r'years=(\d{4})'
    i = 0
    output = []
    for url in urls:
        match = re.search(pattern, url)
        year = None
        if match:
            year = match.group(1)
        
        response = requests.get(url)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        items = soup.find_all('div', class_='video-grid__item')

        if year:
            years[year] += len(items)

        i += 1

        for item in items:
            title = item.find('span', class_='video-grid__item-title').text
            subtitle = item.find('span', class_='video-grid__item-subtitle')
            tournamentName = ""
            division = ""

            if subtitle:
                links = subtitle.find_all('a')
                if len(links) != 2:
                    continue
                else:
                    tournamentName = links[0].text
                    division = links[1].text


            url = item.find('a')['href']
            output.append(Video(title, "", url, tournamentName, division))
    return output
