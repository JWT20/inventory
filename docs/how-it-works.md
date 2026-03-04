# How Wijnpick Works — A Complete Explanation

This document explains the entire system in plain language.
No programming experience required.

---

## What is Wijnpick?

Wijnpick is a warehouse system for wine products. Workers in the warehouse
receive boxes of wine. They take a photo of a box with their phone, and the
system automatically recognizes which wine product it is. Think of it like
Shazam, but for wine boxes instead of songs.

---

## The Big Picture

There are six separate programs ("services") running on your Oracle Cloud
server. They each do one job and talk to each other. Here is what happens
when a warehouse worker takes a photo of a wine box:

```
                                                ┌──────────────┐
  ┌──────────┐       ┌─────────┐       ┌────────┤  Google       │
  │  Worker's │──────▶│ Frontend │──────▶│Backend ├──▶ Gemini AI  │
  │  Phone    │ photo │ (Nginx)  │ photo │(Python)│  (describes   │
  └──────────┘       └─────────┘       └───┬────┤   the photo)  │
                                           │    └──────────────┘
                                           │
                             ┌─────────────┼──────────────┐
                             ▼             ▼              ▼
                        ┌─────────┐  ┌──────────┐  ┌──────────┐
                        │PostgreSQL│  │  Kafka   │  │  Pinot   │
                        │(database)│  │(messages)│  │(analytics)│
                        └─────────┘  └──────────┘  └──────────┘
```

---

## The Six Services, Explained One by One

### 1. Frontend (what the worker sees)

This is the website that workers open on their phone or computer. It shows
buttons, forms, the camera view, and the results. It is a web page built
with JavaScript (Vue.js).

The frontend does not do any thinking on its own. It just shows things to
the user and sends their actions (like "take photo" or "log in") to the
backend.

It runs inside **Nginx**, which is a small web server. Nginx does two jobs:
- Serves the website files (HTML, CSS, JavaScript) to the browser
- Forwards API requests to the backend (acts as a middleman)

**Accessible on:** port 8080 of your server (e.g. `http://your-server:8080`)

---

### 2. Backend (the brain)

This is the main program that does all the work. It is written in Python
using a framework called FastAPI.

When it receives a photo, here is what it does step by step:

1. **Saves the photo** to disk (in the `/app/uploads/scans/` folder)
2. **Sends the photo to Google Gemini** (an AI service) and asks:
   *"Describe this wine box — what brand, vintage, region, colors do you see?"*
3. **Gets back a text description** from Gemini (e.g. "Château Margaux 2018,
   Bordeaux, dark blue box with gold crest")
4. **Converts that description into a list of numbers** (called an "embedding")
   using Gemini's embedding model. This is a mathematical representation of the
   meaning of the text — similar descriptions produce similar numbers.
5. **Compares those numbers** against all known products in the database. This
   uses "cosine similarity" — a way to measure how similar two sets of numbers
   are (1.0 = identical, 0.0 = completely different).
6. **Returns the best match** if the similarity is above the threshold (default:
   0.92 = 92% similar). If it's below that, it says "no match found."

The backend also handles:
- **User login** (username + password, gives you a token to stay logged in)
- **Managing products (SKUs)** — adding, editing, deleting wine products
- **Labels** — generating labels for products
- **Event logging** — recording everything that happens (more on this below)

**Runs on:** port 8000 inside Docker (not directly accessible from outside —
Nginx forwards requests to it)

---

### 3. PostgreSQL (the database)

This is where all permanent data is stored:
- **Users** — who can log in, their roles (admin vs courier)
- **SKUs** — all known wine products (code, name, description)
- **Reference images** — the text descriptions and embeddings for each product

It uses an extension called **pgvector** which lets it store and compare
embeddings (those lists of numbers) efficiently.

Think of it as a filing cabinet. When you add a new wine product, a new
folder goes in the cabinet. When someone scans a box, the system looks
through all the folders to find the closest match.

**Data is permanent** — it survives restarts because it's stored on disk
in a Docker volume called `pgdata`.

---

### 4. Kafka (the message bus)

Kafka is a messaging system. When something happens in the backend (someone
logs in, scans a box, creates a product), the backend writes a small message
("event") to Kafka.

Think of Kafka like a conveyor belt or a mailbox:
- The backend drops messages onto the belt
- Other systems (like Pinot) pick messages off the belt and process them

Why not just write directly to Pinot or a log file? Because Kafka acts as a
buffer. If Pinot is temporarily down, the messages wait in Kafka (up to 7
days) and get processed when Pinot comes back up. The backend never has to
wait or worry about whether Pinot is available.

Kafka runs in **KRaft mode**, which means it manages itself without needing
a separate service called Zookeeper (older Kafka setups needed this).

**Data is kept for:** 7 days (168 hours), then automatically deleted.

---

### 5. Apache Pinot (the analytics database)

Pinot is a specialized database designed for fast analytics queries. It
continuously reads events from Kafka and stores them in a searchable format.

While PostgreSQL stores the main business data (products, users), Pinot
stores the event log — a history of everything that happened:
- Who logged in and when
- What boxes were scanned
- What the AI described in each photo
- How confident the match was
- What product was matched

You can query Pinot using SQL (a language for asking database questions)
to answer questions like:
- "How many boxes were scanned today?"
- "What did the AI say about the last 10 scans?"
- "Which user has been the most active?"

Why a separate database for this? Because PostgreSQL is optimized for
looking up individual records ("give me user #5"), while Pinot is optimized
for scanning through millions of events quickly ("count all scans from
last week grouped by user").

**Pinot-init** is a tiny helper that runs once at startup. It tells Pinot
what the events look like (the schema) and where to find them (the Kafka
topic). After that, it shuts down — it's a one-time setup task.

---

## How They All Connect

Here's the flow from start to finish when a worker scans a wine box:

```
1. Worker opens website on phone
        │
        ▼
2. Browser loads frontend from Nginx (port 8080)
        │
        ▼
3. Worker taps "Scan" and takes a photo
        │
        ▼
4. Browser sends photo to Nginx
        │
        ▼
5. Nginx forwards it to the Backend (port 8000)
        │
        ▼
6. Backend saves the photo to disk
        │
        ▼
7. Backend sends photo to Google Gemini AI
        │
        ▼
8. Gemini looks at the photo and sends back a description:
   "Château Margaux 2018, Grand Vin, Bordeaux, dark label..."
        │
        ▼
9. Backend sends that description back to Gemini's
   embedding model, which converts it into numbers:
   [0.023, -0.187, 0.445, 0.891, ...]  (768 numbers)
        │
        ▼
10. Backend asks PostgreSQL:
    "Which stored product has the most similar numbers?"
        │
        ▼
11. PostgreSQL compares using cosine similarity and returns:
    "SKU WN-042, Château Margaux 2018, similarity: 0.97"
        │
        ▼
12. Backend drops an event into Kafka:
    "box_identified, user=jean, confidence=0.97, sku=WN-042"
        │
        ▼
13. Backend sends the result back to the frontend:
    "Matched: Château Margaux 2018 (97% confidence)"
        │
        ▼
14. Worker sees the result on their phone screen
        │
        ▼
15. Meanwhile, Pinot picks up the event from Kafka
    and stores it for analytics queries
```

---

## The Oracle Cloud Server

All of this runs on a single Oracle Cloud server (a virtual machine in
Oracle's data center). You connect to it via SSH (the terminal).

Docker Compose is the tool that starts and manages all six services
together. When you run `sudo docker compose up -d`, it starts everything.
When you run `sudo docker compose down`, it stops everything. The `-d`
flag means "run in the background" so you can close your terminal.

Your data lives in **Docker volumes** — think of them as virtual hard
drives that persist even when containers are stopped:
- `pgdata` — database data (users, products, embeddings)
- `uploads` — saved photos
- `kafka-data` — Kafka messages (temporary, 7-day retention)
- `pinot-data` — analytics event data

---

## What is Docker?

Docker packages each service into a "container" — a small, isolated
environment that has everything the program needs to run. This means you
don't have to install Python, PostgreSQL, Kafka, etc. directly on your
server. Docker handles all of that.

Think of containers like pre-built appliances. Instead of buying parts and
building a washing machine yourself, you get a ready-made one that you just
plug in. Each service is a separate appliance, and Docker Compose is the
power strip that connects them all.

---

## Summary Table

| Service    | What it does                              | Written in | Port  |
|------------|-------------------------------------------|------------|-------|
| Frontend   | Website the workers use                   | JavaScript | 8080  |
| Nginx      | Serves the website, forwards to backend   | Config     | 8080  |
| Backend    | All the logic: AI, matching, login        | Python     | 8000* |
| PostgreSQL | Stores products, users, embeddings        | -          | 5432* |
| Kafka      | Passes event messages to Pinot            | -          | 9092* |
| Pinot      | Stores & queries event history            | -          | 9000* |

*These ports are only accessible between containers, not from the internet.

---

## Useful Commands

All commands are run from your server terminal in the project directory.

| What you want to do                    | Command                                      |
|----------------------------------------|-----------------------------------------------|
| Start everything                       | `sudo docker compose up -d`                   |
| Stop everything                        | `sudo docker compose down`                    |
| See what's running                     | `sudo docker compose ps`                      |
| See logs from the backend              | `sudo docker compose logs backend --tail 50`  |
| See logs from all services             | `sudo docker compose logs --tail 50`          |
| Restart just the backend               | `sudo docker compose restart backend`         |
| Check recent events in Pinot           | See `docs/event-logging.md`                   |
