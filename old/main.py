from socketserver import ThreadingUnixStreamServer
import threading
from old.webscraper import writeTournamentToCSV, webscrapeTournamentCalendar, get_tournament, NoValidGamesException, InternetConnectionFailedException
import time
import os
import threading
import requests
import queue

class myThread (threading.Thread):
   def __init__(self, threadID, name, rsession, q, fl):
      threading.Thread.__init__(self)
      self.threadID = threadID
      self.name = name
      self.rsession = rsession
      self.q = q
      self.fl = fl
   def run(self):
      print("Starting " + self.name)
      process_data(self.name, self.rsession, self.q, self.fl)
      print("Exiting " + self.name)


def process_data(threadName, rsession, q, fl):
    while not exitFlag:
        queueLock.acquire()
        if not q.empty():
            data = q.get()
            queueLock.release()
            try:
                #scraping tournament data from webpage
                tournament = get_tournament(data[1], rsession, data[3])

                #write this information to the folder and to a specific csv file in that folder
                fileName = data[2] + "/" + (tournament.name).replace("/","_and_") + "_" + tournament.division + "_" + str(data[3]) + ".csv"
                writeTournamentToCSV(fileName, tournament.teams, tournament.games, tournament.name, tournament.division, tournament.datetime.day, tournament.datetime.month, tournament.datetime.year)

                #progress update to user
                print(tournament.name + ", " + tournament.division +  " succesfully scraped and the data has been saved | " + str(data[0]) + " of " + data[4] + " by thread: " + str(threadName))
            
            except NoValidGamesException as e:
                #catches tournament with no valid page or game data
                print(data[1] + " had no valid games to scrape | " + str(data[0]) + " of " + data[4] + " by thread: " + str(threadName))
            except requests.exceptions.RequestException as e:
                #catches errors with beautiful soup accessing tournament
                print(data[1] + " At URLRequest Error, it will be put back in the queue | " + str(data[0]) + " of " + data[4] + " by thread: " + str(threadName))
                print(e)
                queueLock.acquire()
                q.put(data)
                queueLock.release()
            except InternetConnectionFailedException as e:
                #catches errores with Beautiful soup accessing team pages
                print(data[1] + " At URLRequest Error accessing a team page, it will be put back in the queue | " + str(data[0]) + " of " + data[4] + " by thread: " + str(threadName))
                print(e)
                queueLock.acquire()
                q.put(data)
                queueLock.release()
            except Exception as e:
                #logging.exception("An exception was thrown!")
                print(data[1] + " failed to be scraped by thread: " + str(threadName))
                print(e)
                fl.append(data[1])
        else:
            queueLock.release()
        time.sleep(1)

#THE USER NEEDS TO CAHNGE THESE VARIABLES BEFORE USE
url = "https://play.usaultimate.org/events/tournament/?ViewAll=true&IsLeagueType=false&IsClinic=false&FilterByCategory=AE&SeasonId=17"
season_description = "2022"
year = 2022
#^^^^^^^^ THESE ONES

start_time = time.time()
urls = webscrapeTournamentCalendar(url)
exitFlag = 0

#crate directory to hold all the csvs produced from a season
    #name directory
directory = season_description

#filepath of parent directory
# parent_directory = "/Users/matthewmcknight/documents/github/usauWebScraper/"
parent_directory = "."

#join path
path = os.path.join(parent_directory,directory)

#make directory
try:
    os.mkdir(path)
    print("Directory '% s' created" % directory)
except:
    print("file already created")

#finding total number of links for progress update message
num_links = str(len(urls))

#define names of threads and create request sessions
threadList = []
sessionList = []
num_threads = 4
for num in range(1,num_threads+1):
    threadList.append("Thread-" + str(num))
    sessionList.append(requests.Session())

#define workList: contains all the tasks the threads will have to complete
counter = 0
workList = []
for link in urls:
    counter += 1
    workList.append([counter, link,season_description,year,num_links])

#create queue lock to stop threads from doing the same job and create queue to hold 
queueLock = threading.Lock()
workQueue = queue.Queue(int(num_links))
failedList = []
threads = []
threadID = 1

#create new threads
for (tName, session) in zip(threadList, sessionList):
    thread = myThread(threadID, tName, session, workQueue,failedList)
    thread.start()
    threads.append(thread)
    threadID += 1

# Fill the queue
queueLock.acquire()
for work in workList:
   workQueue.put(work)
queueLock.release()

# Wait for queue to empty
while not workQueue.empty():
   pass

# Notify threads it's time to exit
exitFlag = 1


#wait for threads to finish before ending function
for t in threads:
    t.join()

#counting number of files in directory to validate number of tournaments successfully scaped
print(failedList)
print("Successfully scraped " + str(len(os.listdir(path))) + " out of " + str(num_links) + " tournaments") 
print("--- %s seconds ---" % (time.time() - start_time))