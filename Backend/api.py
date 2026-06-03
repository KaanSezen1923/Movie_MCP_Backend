from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional
import uvicorn

# Mevcut server.py dosyasından asıl işi yapan fonksiyonu içeri aktarıyoruz
from server import search_movies_by_filters, GENRE_DICT

app = FastAPI(
    title="TMDB Film Gezgini API",
    description="Streamlit frontend'i için MCP Server arayüzü",
    version="1.0.0"
)

# Streamlit'ten gelecek veri modelini (Payload) tanımlıyoruz
class MovieSearchRequest(BaseModel):
    genre_name: Optional[str] = None
    actor_name: Optional[str] = None
    director_name: Optional[str] = None
    keyword: Optional[str] = None
    min_rating: float = 0.0

@app.post("/api/search")
async def search_movies(request: MovieSearchRequest):
    """
    Belirtilen filtrelere göre server.py içindeki search_movies_by_filters fonksiyonunu tetikler.
    """
    try:
        results_text = search_movies_by_filters(
            genre_name=request.genre_name,
            actor_name=request.actor_name,
            director_name=request.director_name,
            keyword=request.keyword,
            min_rating=request.min_rating
        )
        return {"status": "success", "data": results_text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Sunucu hatası: {str(e)}")

@app.get("/api/genres")
async def get_genres():
    """
    Streamlit arayüzündeki tür seçeneklerini dinamik olarak API'den çekmek istersen.
    """
    return {"status": "success", "data": list(GENRE_DICT.keys())}

if __name__ == "__main__":
    # API'yi 8000 portunda başlatır
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)