# 🎬 Letterboxd Scraper

Letterboxd Scraper is a **free and open-source** scraper that gets you **unlimited** detailed Letterboxd data for free.

## ✨ What Can I Get?

- 🎬 **Full details on 1.2M+ films** — ratings histogram, cast & characters, crew, releases, trailer, IMDb/TMDB IDs & posters
- ✍️ **Every review, in full** — 1M+ on Interstellar alone: full text, rating, likes & comments
- 👤 **Any member's film life** — diary, watchlist, ratings, favorite films, stats & activity feed
- 📈 **Browse like a power user** — popular this week, top rated, 19 genres, 247 countries, 195 languages, themes & where to watch

## 🎥 Example: A Full Letterboxd Film

```json
{
  "id": 117621,
  "name": "Interstellar",
  "year": 2014,
  "link": "https://letterboxd.com/film/interstellar/",
  "tagline": "Mankind was born on Earth. It was never meant to die here.",
  "runtime_minutes": 169,
  "rating": {
    "average": 4.46,
    "count": 6417677,
    "distribution": [{ "rating": 5.0, "count": 3423044, "percent": 53 }]
  },
  "stats": { "watches": 8418275, "lists": 982118, "likes": 4197837, "top_500_rank": 33 },
  "reviews_count": 1030357,
  "directors": [{ "name": "Christopher Nolan", "link": "https://letterboxd.com/director/christopher-nolan/" }],
  "cast": [
    { "name": "Matthew McConaughey", "character": "Cooper", "link": "https://letterboxd.com/actor/matthew-mcconaughey/" },
    { "name": "Anne Hathaway", "character": "Brand", "link": "https://letterboxd.com/actor/anne-hathaway/" }
  ],
  "releases": [{ "type": "premiere", "date": "2014-10-26", "country": "USA", "certification": "PG-13" }],
  "poster": { "link": "https://a.ltrbxd.com/resized/film-poster/1/1/7/6/2/1/117621-interstellar-0-500-0-750-crop.jpg?v=7ad89e6666" },
  "trailer": { "youtube_id": "LY19rHKAaAg", "link": "https://www.youtube.com/watch?v=LY19rHKAaAg" },
  "external_ids": { "imdb": { "id": "tt0816692" }, "tmdb": { "id": 157336 } },
  "similar_films": [{ "name": "Ad Astra", "year": 2019, "link": "https://letterboxd.com/film/ad-astra-2019/" }]
}
```

*Trimmed for readability.*

## 🚀 Unlimited Free Letterboxd Data — Get It in 60 Seconds

1️⃣ Clone and install:
```bash
git clone https://github.com/omkarcloud/letterboxd-scraper
cd letterboxd-scraper
python -m pip install -r requirements.txt
```

2️⃣ Start the API:
```bash
python run.py
```

3️⃣ Get your first data:
```bash
curl "http://localhost:8000/films/details?film=interstellar"
```

```json
{
  "id": 117621,
  "slug": "interstellar",
  "name": "Interstellar",
  "year": 2014,
  "link": "https://letterboxd.com/film/interstellar/",
  "short_link": "https://boxd.it/4VZ8",
  "runtime_minutes": 169,
  "release_date": "2014-10-26",
  "rating": { "average": 4.46, "count": 6417709 },
  "stats": { "watches": 8418309, "lists": 982111, "likes": 4197858, "top_500_rank": 33 },
  "directors": [
    { "name": "Christopher Nolan", "slug": "christopher-nolan", "link": "https://letterboxd.com/director/christopher-nolan/" }
  ],
  "trailer": { "youtube_id": "LY19rHKAaAg", "link": "https://www.youtube.com/watch?v=LY19rHKAaAg" },
  "external_ids": {
    "imdb": { "id": "tt0816692", "link": "https://www.imdb.com/title/tt0816692/" },
    "tmdb": { "id": 157336, "type": "movie", "link": "https://www.themoviedb.org/movie/157336/" }
  }
}
```

All 35 endpoints are now live at `http://localhost:8000`.

## 📚 Endpoints

35 endpoints cover everything you need.

| Endpoint | Path | Returns |
|---|---|---|
| Film Details | `/films/details` | Everything about one film in a single call |
| Autocomplete Films | `/films/autocomplete` | Type-ahead suggestions with year, runtime and directors |
| Search | `/search` | Films, reviews, lists, cast & crew, members or tags |
| Film Stats | `/films/stats` | Ratings histogram, watches, likes, lists and Top 500 rank |
| Where to Watch | `/films/availability` | Stream, rent or buy offers with prices, in any country |
| Film Reviews / Lists / Members | `/films/reviews`, `/films/lists`, `/films/members` | Full-text reviews, lists featuring it, watchers, fans and likers |
| Similar Films / Themes | `/films/similar`, `/films/themes` | Up to 72 lookalikes, plus themes with matching films |
| Film Poster | `/films/poster` | Poster links at any width, plus a 2x version |
| Browse Films | `/films/browse` | 72 films per page, filtered by genre, decade, country, language |
| Film Collection | `/films/collection` | Every film in a franchise, with ratings and posters |
| Person Details | `/people/details` | Bio, photo, every role and the full filmography |
| Tag | `/tags` | Films, lists or reviews members tagged |
| Review Details / Comments | `/reviews/details`, `/reviews/comments` | One review in full, plus its comment thread |
| List Details / Comments | `/lists/details`, `/lists/comments` | Ranked films with owner notes and ratings, plus comments |
| Popular Reviews / Lists / Members | `/reviews/popular`, `/lists/popular`, `/members/popular` | What's hot this week, month, year or all time |
| Member Profile | `/members/details` | Bio, counts, favorite films and ratings histogram |
| Member Films / Diary / Watchlist | `/members/films`, `/members/diary`, `/members/watchlist` | Everything they watched, logged and want to watch |
| Member Reviews / Lists / Likes | `/members/reviews`, `/members/lists`, `/members/likes` | Their reviews, lists and liked films, reviews or lists |
| Member Followers / Following | `/members/followers`, `/members/following` | Their network, with each member's film counts |
| Member Activity | `/members/activity` | Live feed of watches, ratings, likes and reviews |
| Member Stats | `/members/stats` | Hours watched, top genres, countries, actors and directors |
| Member Tags | `/members/tags` | The tags they use and how often |
| Journal Articles | `/journal` | Latest Letterboxd Journal articles with teasers and images |


## 🔍 Exploring Parameters

The same API is published on RapidAPI, and its playground is the easiest place to try parameters and see raw responses. Once a request looks right, run it locally for **unlimited free** data.

1. [Subscribe to the free plan](https://rapidapi.com/OmkarCloud/api/best-letterboxd-scraper-free-1000-calls/pricing) — 1,000 calls/month, no credit card.
2. [Try the endpoints in the playground](https://rapidapi.com/OmkarCloud/api/best-letterboxd-scraper-free-1000-calls/playground) — every param is pre-filled, so you see real data in one click.
3. Copy the generated code and replace `https://best-letterboxd-scraper-free-1000-calls.p.rapidapi.com` with `http://localhost:8000`. It will now run against your local API.

```python
import requests

# generated by the playground, host swapped for the local API
response = requests.get(
    "http://localhost:8000/films/details",
    params={"film": "interstellar"},
)
print(response.json())
```

## 💬 Have Questions? We Have Answers.

You're a developer — we know how hard completing a project can be. So we offer full support: just message us and we'll reply ✅ with a solution within 1 working day.

[![Message Us on WhatsApp about Letterboxd Scraper](https://raw.githubusercontent.com/omkarcloud/assets/master/images/whatsapp-us.png)](https://www.omkar.cloud/l/letterboxd-scraper-github-whatsapp)

[![Ask Us by Email about Letterboxd Scraper](https://raw.githubusercontent.com/omkarcloud/assets/master/images/ask-on-email.png)](https://www.omkar.cloud/l/letterboxd-scraper-github-email)

## ⚡ Popular Scrapers by Omkar Cloud

- [**Google Maps Scraper (3,100+ GitHub Stars)**](https://github.com/omkarcloud/google-maps-scraper) — type "dentists in New York", get every business as a ready-to-call lead list: phones, emails, websites & reviews. Up to 100K free leads/month.
- [**IMDb Scraper**](https://github.com/omkarcloud/imdb-scraper) — IMDb titles: ratings, cast, box office, reviews & charts
- [**G2 Scraper**](https://www.omkar.cloud/tools/g2-scraper) — G2 product details, ratings & AI-found contacts
- [**Website Email Contact Scraper**](https://www.omkar.cloud/tools/website-email-contact-scraper) — emails, phones & socials from any website
- [**AliExpress Scraper**](https://www.omkar.cloud/tools/aliexpress-scraper-api) — live product details, SKU variants, stock & shipping
- [**Booking Scraper**](https://www.omkar.cloud/tools/booking-scraper) — Booking.com hotels: prices, ratings, rooms & amenities

## ⭐ Love It? [Star It ⭐!](https://github.com/omkarcloud/letterboxd-scraper)

Star the repo ⭐ and become my star hero!

It's just 1 click, but it means the world to me.

[![Star us on GitHub](https://raw.githubusercontent.com/omkarcloud/google-maps-scraper/master/screenshots/star-us.png)](https://github.com/omkarcloud/letterboxd-scraper)
