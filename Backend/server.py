import os
import requests
from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv

load_dotenv()
AUTH_KEY = os.getenv("AUTH_KEY")
BASE_URL = "https://api.themoviedb.org/3"

mcp = FastMCP("TMDB Movie Explorer")

headers = {
    "accept": "application/json",
    "Authorization": f"Bearer {AUTH_KEY}"
}

GENRE_DICT = {
    "Action": 28, "Adventure": 12, "Animation": 16, "Comedy": 35,
    "Crime": 80, "Documentary": 99, "Drama": 18, "Family": 10751,
    "Fantasy": 14, "History": 36, "Horror": 27, "Music": 10402,
    "Mystery": 9648, "Romance": 10749, "Science Fiction": 878,
    "TV Movie": 10770, "Thriller": 53, "War": 10752, "Western": 37
}

REVERSE_GENRE_DICT = {v: k for k, v in GENRE_DICT.items()}

# --- Yardımcı Fonksiyonlar ---

def get_person_id(name: str):
    search_url = f"{BASE_URL}/search/person"
    params = {"query": name, "language": "en-US"}
    resp = requests.get(search_url, headers=headers, params=params)
    results = resp.json().get('results', [])
    return results[0]['id'] if results else None

def get_keyword_id(keyword: str):
    search_url = f"{BASE_URL}/search/keyword"
    params = {"query": keyword}
    resp = requests.get(search_url, headers=headers, params=params)
    results = resp.json().get('results', [])
    return results[0]['id'] if results else None

def get_movie_id(title: str):
    search_url = f"{BASE_URL}/search/movie"
    params = {"query": title, "language": "en-US"}
    resp = requests.get(search_url, headers=headers, params=params)
    results = resp.json().get('results', [])
    return results[0]['id'] if results else None

def get_movie_trailer_url(movie_title: str) -> str:
    """Sadece YouTube fragman URL'sini (veya None) döner."""
    movie_id = get_movie_id(movie_title)
    if not movie_id:
        return None

    video_url = f"{BASE_URL}/movie/{movie_id}/videos"
    resp = requests.get(video_url, headers=headers, params={"language": "en-US"})
    if resp.status_code != 200:
        return None

    videos = resp.json().get('results', [])
    trailer = next((v for v in videos if v['type'] == 'Trailer' and v['site'] == 'YouTube'), None)
    if not trailer and videos:
        trailer = videos[0]

    return f"https://www.youtube.com/watch?v={trailer['key']}" if trailer else None

def get_watch_platforms(movie_id: int) -> list:
    """Platform isimlerini ham bir liste (list) olarak döner."""
    watch_url = f"{BASE_URL}/movie/{movie_id}/watch/providers"
    resp = requests.get(watch_url, headers=headers, params={"watch_region": "TR"})
    
    platforms = []
    if resp.status_code == 200:
        results = resp.json().get('results', {}).get('TR', {})
        if 'flatrate' in results:
            platforms = [p['provider_name'] for p in results['flatrate']]
            
    return platforms

def get_movie_credits(movie_id: int):
    """Filmin yönetmen ve başrol oyuncularını döner."""
    credits_url = f"{BASE_URL}/movie/{movie_id}/credits"
    resp = requests.get(credits_url, headers=headers)
    
    if resp.status_code != 200:
        return "Bilinmiyor", "Bilinmiyor"

    data = resp.json()
    cast = [c['name'] for c in data.get('cast', [])[:3]]
    cast_str = ", ".join(cast) if cast else "Bilinmiyor"
    director = next((c['name'] for c in data.get('crew', []) if c['job'] == 'Director'), "Bilinmiyor")
    
    return director, cast_str

# --- Çekirdek Veri Fonksiyonu (API İçin Temiz JSON) ---
def fetch_movies_json(genre_name=None, actor_name=None, director_name=None, keyword=None, min_rating=0.0) -> list:
    """
    Kriterlere göre filmleri bulup temiz bir Sözlük (Dictionary) listesi döner.
    Arayüz uygulamaları Regex'e bulaşmadan bu fonksiyonu (veya API endpointini) kullanır.
    """
    discover_url = f"{BASE_URL}/discover/movie"
    params = {
        "include_adult": "false",
        "language": "en-US",
        "page": 1,
        "sort_by": "popularity.desc"
    }

    if genre_name:
        gid = GENRE_DICT.get(genre_name.title())
        if gid: params["with_genres"] = gid
    if actor_name:
        aid = get_person_id(actor_name)
        if aid: params["with_cast"] = aid
    if director_name:
        did = get_person_id(director_name)
        if did: params["with_crew"] = did
    if keyword:
        kid = get_keyword_id(keyword)
        if kid: params["with_keywords"] = kid
    if min_rating > 0:
        params["vote_average.gte"] = min_rating
        params["vote_count.gte"] = 50 

    resp = requests.get(discover_url, headers=headers, params=params)
    if resp.status_code != 200:
        return []

    movies = resp.json().get('results', [])[:10]
    
    output_data = []
    for m in movies:
        movie_id = m.get('id')
        title = m.get('title', 'Bilinmiyor')
        director, cast = get_movie_credits(movie_id)
        g_ids = m.get('genre_ids', [])
        poster = m.get('poster_path')
        
        movie_dict = {
            "title": title,
            "year": m.get('release_date', '????')[:4],
            "rating": m.get('vote_average', 0),
            "genres": ", ".join([REVERSE_GENRE_DICT.get(gid, "Unknown") for gid in g_ids]),
            "director": director,
            "cast": cast,
            "overview": m.get('overview', 'Özet bulunamadı.'),
            "platforms": get_watch_platforms(movie_id),
            "poster_url": f"https://image.tmdb.org/t/p/w500{poster}" if poster else None,
            "trailer_url": get_movie_trailer_url(title)
        }
        output_data.append(movie_dict)
        
    return output_data

# --- MCP Tool Tanımı (LLM İçin Sarmalayıcı) ---
@mcp.tool()
def search_movies_by_filters(genre_name: str=None, actor_name: str=None, director_name: str=None, keyword: str=None, min_rating: float=0.0) -> str:
    """
    Belirli kriterlere göre film araması yapar. 
    LLM bu aracı; kullanıcı bir aktör, yönetmen, tür veya minimum puan belirttiğinde kullanır.
    """
    movies_data = fetch_movies_json(genre_name, actor_name, director_name, keyword, min_rating)
    
    if not movies_data:
        return "Aradığınız kriterlere uygun film bulunamadı."

    # JSON verisini LLM'in okuyabileceği temiz bir metne dönüştür
    output = []
    for m in movies_data:
        platforms_str = f"📺 İzleyebileceğin Platformlar: {', '.join(m['platforms'])}" if m['platforms'] else "🚫 Şu an popüler bir platformda yayında değil."
        
        movie_info = (
            f"🎬 {m['title']} ({m['year']})\n"
            f"⭐ Puan: {m['rating']} | 🎭 Türler: {m['genres']}\n"
            f"👤 Yönetmen: {m['director']} | 👥 Oyuncular: {m['cast']}\n"
            f"📝 Özet: {m['overview'][:150]}...\n"
            f"{platforms_str}\n"
        )
        if m['poster_url']:
            movie_info += f"🖼️ Poster: {m['poster_url']}\n"
        if m['trailer_url']:
            movie_info += f"🔗 Fragman: {m['trailer_url']}\n"

        output.append(movie_info + "-" * 30)
    
    return "\n".join(output)

if __name__ == "__main__":
    mcp.run()