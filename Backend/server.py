import os
import requests
from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv

# Yapılandırma
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

# --- Yardımcı Fonksiyonlar (Tool Olarak Tanımlanmadı) ---

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

def get_movie_trailer(movie_title: str) -> str:
    """
    Bir filmin ismine göre YouTube fragman bağlantısını bulur.
    Kullanıcı bir filmin fragmanını, videosunu veya 'izlemek istiyorum' dediğinde bu aracı kullan.
    """
    movie_id = get_movie_id(movie_title)
    if not movie_id:
        return f"'{movie_title}' isimli film bulunamadı."

    video_url = f"{BASE_URL}/movie/{movie_id}/videos"
    params = {"language": "en-US"}
    resp = requests.get(video_url, headers=headers, params=params)
    
    if resp.status_code != 200:
        return "Video verileri alınırken bir hata oluştu."

    videos = resp.json().get('results', [])
    
    # Fragman (Trailer) tipinde ve YouTube üzerinde olan bir video ara
    trailer = next(
        (v for v in videos if v['type'] == 'Trailer' and v['site'] == 'YouTube'), 
        None
    )

    # Eğer spesifik bir fragman yoksa herhangi bir videoyu al
    if not trailer and videos:
        trailer = videos[0]

    if trailer:
        youtube_url = f"https://www.youtube.com/watch?v={trailer['key']}"
        return f"🎬 {movie_title} Fragmanı:\n🔗 {youtube_url}\n📺 Tip: {trailer['type']}"
    
    return f"'{movie_title}' için uygun bir fragman bulunamadı."

def get_watch_platforms(movie_id: int) -> str:
    """
    Filmin Türkiye'deki (veya genel) izleme platformlarını döner.
    """
    watch_url = f"{BASE_URL}/movie/{movie_id}/watch/providers"
    # TR pazarındaki platformlar için 'watch_region' parametresini kullanıyoruz
    params = {"watch_region": "TR"} 
    resp = requests.get(watch_url, headers=headers, params=params)
    
    if resp.status_code != 200:
        return "Platform bilgisi alınamadı."

    results = resp.json().get('results', {}).get('TR', {})
    
    platforms = []
    
    # 'flatrate' abonelik tabanlı (Netflix vb.), 'buy' ise satın alma seçenekleridir
    if 'flatrate' in results:
        for provider in results['flatrate']:
            platforms.append(provider['provider_name'])
            
    if platforms:
        return f"📺 İzleyebileceğin Platformlar: {', '.join(platforms)}"
    
    return "🚫 Şu an popüler bir platformda yayında değil (Sadece kiralama/satın alma olabilir)."

# --- MCP Tool Tanımı ---
def get_movie_credits(movie_id: int):
    """Filmin yönetmen ve başrol oyuncularını getirir."""
    credits_url = f"{BASE_URL}/movie/{movie_id}/credits"
    resp = requests.get(credits_url, headers=headers)
    
    if resp.status_code != 200:
        return "Bilinmiyor", "Bilinmiyor"

    data = resp.json()
    
    # İlk 3 oyuncuyu al
    cast = [c['name'] for c in data.get('cast', [])[:3]]
    cast_str = ", ".join(cast) if cast else "Bilinmiyor"
    
    # Yönetmeni bul
    director = next((c['name'] for c in data.get('crew', []) if c['job'] == 'Director'), "Bilinmiyor")
    
    return director, cast_str

@mcp.tool()
def search_movies_by_filters(
    genre_name: str = None, 
    actor_name: str = None, 
    director_name: str = None, 
    keyword: str = None, 
    min_rating: float = 0.0
) -> str:
    """
    Belirli kriterlere göre film araması yapar. 
    LLM bu aracı; kullanıcı bir aktör, yönetmen, tür veya minimum puan belirttiğinde kullanır.
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
        params["vote_count.gte"] = 50  # Kaliteli sonuçlar için

    resp = requests.get(discover_url, headers=headers, params=params)
    
    if resp.status_code != 200:
        return f"Hata oluştu: {resp.status_code}"

    movies = resp.json().get('results', []) # İlk 10 sonucu dön
    
    if not movies:
        return "Aradığınız kriterlere uygun film bulunamadı."

    # Sonuçları LLM'in okuyabileceği temiz bir metne dönüştür
    output = []
    for m in movies:
        movie_id = m.get('id')
        platform_info = get_watch_platforms(movie_id)
        director, cast = get_movie_credits(movie_id)
        title = m.get('title', 'Bilinmiyor')
        year = m.get('release_date', '????')[:4]
        rating = m.get('vote_average', 0)
        overview = m.get('overview', 'Özet bulunamadı.')[:150]
        poster = m.get('poster_path')

        
        # Tür ID'lerini isimlere çevir
        g_ids = m.get('genre_ids', [])
        g_names = [REVERSE_GENRE_DICT.get(gid, "Unknown") for gid in g_ids]
        genres_str = ", ".join(g_names)
        trailer=get_movie_trailer(title)

        movie_info = (
            f"🎬 {title} ({year})\n"
            f"⭐ Puan: {rating} | 🎭 Türler: {genres_str}\n"
            f"👤 Yönetmen: {director} | 👥 Oyuncular: {cast}\n"
            f"📝 Özet: {overview}...\n"
            f"📺 Platformlar: {platform_info}\n"
        )
        if poster:
            movie_info += f"🖼️ Poster: https://image.tmdb.org/t/p/w500{poster}\n"
        if trailer:
            movie_info += f"{trailer}\n"

        output.append(movie_info + "-" * 30)
    
    return "\n".join(output)




if __name__ == "__main__":
    mcp.run()