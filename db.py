import sqlite3

def create_tournaments_db():
    # Connect to the database
    conn = sqlite3.connect('scraper.db')
    c = conn.cursor()
    
    # Create the Tournaments table
    c.execute('''CREATE TABLE IF NOT EXISTS Tournaments
                (csvPath TEXT PRIMARY KEY, needsUpdate INTEGER, updatedAt TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    
    # Commit the changes and close the connection
    conn.commit()
    conn.close()

def updateSuccesfulCSVs(csv_list):
    updateCSVs(csv_list, 0)

def updateFailedCSVs(csv_list):
    updateCSVs(csv_list, 1)

def updateCSVs(csv_list, needsUpdate):
    # Connect to the database
    conn = sqlite3.connect('scraper.db')
    c = conn.cursor()
    
    for csv in csv_list:
        # Check if the row exists in the table
        c.execute("SELECT * FROM Tournaments WHERE csvPath=?", (csv,))
        row = c.fetchone()
        if row is None:
            # If the row does not exist, insert a new row with needsUpdate set to 1
            c.execute("INSERT INTO Tournaments (csvPath, needsUpdate) VALUES (?, ?)", (csv, needsUpdate))
        else:
            # If the row exists, update the needsUpdate value to 1
            c.execute("UPDATE Tournaments SET needsUpdate=? WHERE csvPath=?", (needsUpdate, csv))
    
    # Commit the changes and close the connection
    conn.commit()
    conn.close()


