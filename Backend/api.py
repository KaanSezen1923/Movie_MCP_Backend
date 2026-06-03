from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional
import uvicorn
from server import fetch_movies_json, GENRE_DICT

app = FastAPI(
    title="TMDB Film Gezgini API",
    description="Streamlit frontend'i için MCP Server arayüzü",
    version="1.0.0"
)

class MovieSearchRequest(BaseModel):
    genre_name: Optional[str] = None
    actor_name: Optional[str] = None
    director_name: Optional[str] = None
    keyword: Optional[str] = None
    min_rating: float = 0.0

@app.post("/api/search")
async def search_movies(request: MovieSearchRequest):
    """
    Belirtilen filtrelere göre temiz JSON formatında film verisi döndürür.
    """
    try:
        movies_list = fetch_movies_json(
            genre_name=request.genre_name,
            actor_name=request.actor_name,
            director_name=request.director_name,
            keyword=request.keyword,
            min_rating=request.min_rating
        )
        return {"status": "success", "data": movies_list}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Sunucu hatası: {str(e)}")

@app.get("/api/genres")
async def get_genres():
    return {"status": "success", "data": list(GENRE_DICT.keys())}

if __name__ == "__main__":
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)