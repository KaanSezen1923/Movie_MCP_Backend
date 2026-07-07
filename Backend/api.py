import os
import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from fastapi import FastAPI, HTTPException,BackgroundTasks, UploadFile, File
from httpx import request
from pydantic import BaseModel,EmailStr, field_validator
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from dotenv import load_dotenv
from shared import ctx
from client import get_ollama_tools, generate_user_profile, app as graph_app
import psycopg2
import bcrypt
import whisper
import tempfile
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from exponent_server_sdk import PushClient, PushMessage
import random 
import uvicorn 
from groq import Groq
import wave
import io
import requests
from fastapi.responses import StreamingResponse, FileResponse

import ollama 

load_dotenv()

client = Groq(api_key=os.environ.get("WHISPER_API_KEY"))

class FavoriteRequest(BaseModel):
    user_id: int
    movie_id: str
    title: str
    genres: str = None
    director: str = None      
    cast_members: str = None   
    poster_url: str = None
    imdb_rating: str = None

class TranscriptionResponse(BaseModel):
    text: str
    success: bool



class TokenRequest(BaseModel):
    user_id: int
    token: str

def remove_file(path: str):
    """Arka planda geçici dosyaları silmek için yardımcı fonksiyon"""
    if os.path.exists(path):
        os.remove(path)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global whisper_model
    server_params = StdioServerParameters(
        command="python",
        args=["server.py"], # Kendi MCP server dosyanızın adı
        env={"AUTH_KEY": os.getenv("AUTH_KEY")}
    )

    print("🚀 MCP Sunucusu ve Session başlatılıyor...")
    try:
        # shared içindeki ctx nesnesini dolduruyoruz
        read, write = await ctx.exit_stack.enter_async_context(stdio_client(server_params))
        ctx.session = await ctx.exit_stack.enter_async_context(ClientSession(read, write))
        
        await ctx.session.initialize()
        
        # Tool'ları çekip shared ctx içine kaydediyoruz
        ctx.tools = await get_ollama_tools(ctx.session)
        print(f"✅ {len(ctx.tools)} adet tool başarıyla yüklendi.")

        scheduler = AsyncIOScheduler()
        scheduler.add_job(send_random_notifications, 'interval', minutes=1)  # Her saat başı çalışacak şekilde ayarlandı
        scheduler.start()
        print("⏰ Zamanlayıcı (Scheduler) başlatıldı.")
        
        yield
    finally:
        scheduler.shutdown()
        print("🛑 Sunucu kapatılıyor, kaynaklar temizleniyor...")
        await ctx.exit_stack.aclose()



app = FastAPI(title="Movie Explorer AI API", lifespan=lifespan)


def get_db_connection():
    return psycopg2.connect(
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        host=os.getenv("DB_HOST")
    )


class UserSignup(BaseModel):
    username: str
    email: EmailStr # Email formatı kontrolü yapar
    password: str

    @field_validator('password')
    @classmethod
    def truncate_password(cls, v: str) -> str:
        # Bcrypt 72 byte sınırı için 71'de kesiyoruz
        return v[:71]

class UserLogin(BaseModel):
    username: str
    password: str

class UpdateProfileRequest(BaseModel):
    user_id: int
    new_username: str

class ChangePasswordRequest(BaseModel):
    user_id: int
    old_password: str
    new_password: str


class ChatRequest(BaseModel):
    prompt: str
    user_id: int
    session_id: str

class ChatResponse(BaseModel):
    answer: str
    tool_calls: list = []   # Modelin hangi tool'u çağırdığı + argümanlar
    tool_results: list = [] # MCP server'dan dönen ham veriler

def hash_password(password: str):
    # Şifreyi byte formatına çevirip hashliyoruz
    pwd_bytes = password.encode('utf-8')
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(pwd_bytes, salt)
    return hashed.decode('utf-8') # Veritabanına string olarak kaydetmek için

def verify_password(plain_password, hashed_password):
    # plain_password: Kullanıcıdan gelen düz metin
    # hashed_password: Veritabanından gelen hashli metin
    password_byte = plain_password.encode('utf-8')
    hashed_byte = hashed_password.encode('utf-8')
    return bcrypt.checkpw(password_byte, hashed_byte)

def save_chat_to_db(user_id: int, role: str, content: str, session_id: str):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO chat_history (user_id, role, content, session_id) VALUES (%s, %s, %s, %s)",
            (user_id, role, content, session_id)
        )
        conn.commit()
    except Exception as e:
        print(f"❌ DB Kayıt Hatası: {e}")
        if conn: conn.rollback()
    finally:
        if conn: conn.close()

def get_user_persona(user_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT persona_summary FROM user_profiles WHERE user_id = %s",
        (user_id,)
    )
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else None

def get_recent_chats(user_id: int, limit: int = 10):
    conn = get_db_connection()
    cursor = conn.cursor()
    # Son mesajları al
    cursor.execute(
        "SELECT role, content FROM chat_history WHERE user_id = %s ORDER BY created_at DESC LIMIT %s",
        (user_id, limit)
    )
    chats = cursor.fetchall()
    conn.close()
    
    # HATALI KISIM BURASIYDI: "\n".join(...) yerine liste döndürüyoruz
    return [{"role": c[0], "content": c[1]} for c in reversed(chats)]

def get_user_favorites(user_id: int):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Veritabanından o kullanıcıya ait tüm favorileri çekiyoruz
        query = """
            SELECT movie_id, title, genres, director, cast_members, poster_url, imdb_rating 
            FROM favorites 
            WHERE user_id = %s 
            ORDER BY created_at DESC
        """
        cursor.execute(query, (user_id,))
        rows = cursor.fetchall()
        
        # Verileri frontend'in beklediği MovieData formatına sokuyoruz
        favorites = [
            {
                "movie_id": r[0],
                "Film": r[1],          # Card.tsx 'Film' anahtarını bekliyor
                "Türler": r[2],        # Card.tsx 'Türler' anahtarını bekliyor
                "Director": r[3],
                "Cast": r[4],
                "Poster": r[5],
                "IMDb": r[6]
            } for r in rows
        ]
        
        return {"favorites": favorites}
    except Exception as e:
        print(f"❌ Favori getirme hatası: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            cursor.close()
            conn.close()

def update_persona_in_db(user_id: int, summary: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE user_profiles SET persona_summary = %s, last_updated = CURRENT_TIMESTAMP WHERE user_id = %s",
        (summary, user_id)
    )
    conn.commit()
    conn.close()

async def profile_update_task(user_id: int):
    try:
        # 1. Sohbet geçmişini al
        history_list = get_recent_chats(user_id, limit=10)
        history_text = "\n".join([f"{m['role']}: {m['content']}" for m in history_list])
        
        # 2. Kullanıcının favorilerini veritabanından al (Yeni)
        # Varsayım: get_user_favorites fonksiyonu ['Inception', 'Nolan', 'Sci-Fi'] gibi bir liste döner.
        favorites_list = get_user_favorites(user_id) 
        favorites_text = ", ".join(favorites_list) if favorites_list else "Henüz favori eklenmemiş."

        # 3. Profil oluşturma fonksiyonuna her iki veriyi de gönder
        new_profile = await generate_user_profile(history_text, favorites_text)
        
        update_persona_in_db(user_id, new_profile)
        print(f"✅ Kullanıcı {user_id} için profil güncellendi.")
    except Exception as e:
        print(f"❌ Profilleme Hatası: {e}")
@app.post("/signup")
async def signup(user: UserSignup):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # 1. Kullanıcı adı veya Email var mı kontrol et
        cursor.execute("SELECT id FROM users WHERE username = %s OR email = %s", (user.username, user.email))
        if cursor.fetchone():
            raise HTTPException(status_code=400, detail="Kullanıcı adı veya email zaten kayıtlı.")
        
        # 2. Şifreyi hashle
        hashed_pwd = hash_password(user.password)
        
        # 3. Users tablosuna ekle
        cursor.execute(
            "INSERT INTO users (username, email, password_hash) VALUES (%s, %s, %s) RETURNING id",
            (user.username, user.email, hashed_pwd)
        )
        new_user_id = cursor.fetchone()[0]
        
        # 4. User_profiles tablosuna başlangıç kaydı ekle (ÖNEMLİ)
        cursor.execute(
            "INSERT INTO user_profiles (user_id) VALUES (%s)",
            (new_user_id,)
        )
        
        conn.commit()
        return {"message": "Kayıt başarılı! Giriş yapabilirsiniz."}

    except Exception as e:
        if conn: conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            cursor.close()
            conn.close()

@app.post("/login")
async def login(user: UserLogin):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Kullanıcıyı bul
        cursor.execute("SELECT id, password_hash FROM users WHERE username = %s", (user.username,))
        record = cursor.fetchone()
        
        if record and verify_password(user.password, record[1]):
            # Giriş başarılı - Şimdilik ID dönüyoruz, React Native'de buraya JWT gelecek
            return {
                "status": "success",
                "user_id": record[0],
                "username": user.username,
                "message": f"Hoş geldin {user.username}!"
            }
        
        raise HTTPException(status_code=401, detail="Hatalı kullanıcı adı veya şifre.")

    except Exception as e:
        if isinstance(e, HTTPException): raise e
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            cursor.close()
            conn.close()

@app.post("/update-profile")
async def update_profile(req: UpdateProfileRequest):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Check if username is already taken
        cursor.execute("SELECT id FROM users WHERE username = %s AND id != %s", (req.new_username, req.user_id))
        if cursor.fetchone():
            raise HTTPException(status_code=400, detail="Bu kullanıcı adı zaten alınmış.")
            
        cursor.execute("UPDATE users SET username = %s WHERE id = %s", (req.new_username, req.user_id))
        conn.commit()
        return {"status": "success", "username": req.new_username}
    except Exception as e:
        if isinstance(e, HTTPException): raise e
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            cursor.close()
            conn.close()

@app.post("/change-password")
async def change_password(req: ChangePasswordRequest):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Get current hash
        cursor.execute("SELECT password_hash FROM users WHERE id = %s", (req.user_id,))
        record = cursor.fetchone()
        if not record or not verify_password(req.old_password, record[0]):
            raise HTTPException(status_code=400, detail="Mevcut şifreniz hatalı.")
            
        # Hash new password
        hashed_pwd = hash_password(req.new_password)
        cursor.execute("UPDATE users SET password_hash = %s WHERE id = %s", (hashed_pwd, req.user_id))
        conn.commit()
        return {"status": "success", "message": "Şifreniz başarıyla değiştirildi."}
    except Exception as e:
        if isinstance(e, HTTPException): raise e
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            cursor.close()
            conn.close()



@app.get("/sessions/{user_id}")
async def get_sessions(user_id: int):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        query = """
            SELECT session_id, 
                   MIN(created_at) as created,
                   (SELECT content FROM chat_history ch2 WHERE ch2.session_id = ch1.session_id AND role='user' ORDER BY created_at ASC LIMIT 1) as title
            FROM chat_history ch1
            WHERE user_id = %s
            GROUP BY session_id
            ORDER BY created DESC
        """
        cursor.execute(query, (user_id,))
        rows = cursor.fetchall()
        sessions = [{"session_id": r[0], "title": r[2] if r[2] else "Yeni Sohbet"} for r in rows]
        return {"sessions": sessions}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            cursor.close()
            conn.close()

@app.get("/chat/{user_id}/{session_id}")
async def get_chat_history(user_id: int, session_id: str):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT role, content FROM chat_history WHERE user_id = %s AND session_id = %s ORDER BY created_at ASC",
            (user_id, session_id)
        )
        rows = cursor.fetchall()
        history = [{"role": r[0], "content": r[1]} for r in rows]
        return {"history": history}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            cursor.close()
            conn.close()

@app.delete("/chat/{user_id}/{session_id}")
async def delete_session(user_id: int, session_id: str):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM chat_history WHERE user_id = %s AND session_id = %s", (user_id, session_id))
        conn.commit()
        return {"status": "success"}
    except Exception as e:
        if conn: conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            cursor.close()
            conn.close()

@app.post("/transcribe", response_model=TranscriptionResponse)
async def transcribe_audio(audio: UploadFile = File(...)):
    temp_file_path = None
    try:
        file_ext = os.path.splitext(audio.filename)[1].lower()
        if not file_ext:
            file_ext = ".wav"
            
        with tempfile.NamedTemporaryFile(delete=False, suffix=file_ext) as temp_file:
            content = await audio.read()
            if not content:
                raise HTTPException(status_code=400, detail="Dosya içeriği boş.")
            temp_file.write(content)
            temp_file_path = temp_file.name

        # Groq API'sine Whisper isteği gönder
        with open(temp_file_path, "rb") as audio_file:
            transcription = client.audio.transcriptions.create(
                model="whisper-large-v3", 
                file=audio_file,
                language="tr" 
            )

        return TranscriptionResponse(text=transcription.text.strip(), success=True)

    except Exception as e:
        print(f"Hata Detayı: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Transkripsiyon hatası: {str(e)}")
    finally:
        # Geçici dosyayı temizle
        if temp_file_path and os.path.exists(temp_file_path):
            os.remove(temp_file_path)





@app.post("/chat")
async def chat(request: ChatRequest, background_tasks: BackgroundTasks):
    if not ctx.session:
        raise HTTPException(status_code=503, detail="MCP Session hazır değil.")
    
    user_persona = get_user_persona(request.user_id)
    chat_history = get_recent_chats(request.user_id, limit=10) 
    
    initial_state = {
        "prompt": request.prompt,
        "persona": user_persona or "Film sever bir kullanıcı.",
        "messages": chat_history + [{"role": "user", "content": request.prompt}],
        "tools": ctx.tools, # client.py'daki düğüm bunu kullanacak
        "intent": "",
        "final_output": "",
        "tool_calls": [],
        "tool_results": []
    }
    
    try:
        # graph_app (client.py'daki app) çağrılıyor
        result = await graph_app.ainvoke(initial_state) 
        answer = result.get("final_output", "Üzgünüm, şu an öneri yapamıyorum.")
        tool_calls = result.get("tool_calls", [])
        tool_results = result.get("tool_results", [])
    except Exception as e:
        print(f"Grafik Hatası: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    save_chat_to_db(request.user_id, "user", request.prompt, request.session_id)
    save_chat_to_db(request.user_id, "assistant", answer, request.session_id)

    # 6. Persona Güncelleme Kontrolü (Arka plan görevi)
    msg_count = get_user_message_count(request.user_id)
    if msg_count > 0 and msg_count % 5 == 0:
        background_tasks.add_task(profile_update_task, request.user_id)

    return {"answer": answer, "tool_calls": tool_calls, "tool_results": tool_results}

def get_user_message_count(user_id: int):
    conn = get_db_connection()

    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM chat_history WHERE user_id = %s", (user_id,))

    msg_count = cursor.fetchone()[0]

    conn.close()
    return msg_count

@app.post("/favorites")
async def add_favorite(fav: FavoriteRequest):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        query = """
            INSERT INTO favorites (user_id, movie_id, title, genres, director, cast_members, poster_url, imdb_rating)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (user_id, movie_id) DO NOTHING
        """
        cursor.execute(query, (
            fav.user_id, fav.movie_id, fav.title, 
            fav.genres, fav.director, fav.cast_members, # Yeni alanlar eklendi
            fav.poster_url, fav.imdb_rating
        ))
        
        conn.commit()
        return {"status": "success", "message": "Film detaylarıyla birlikte favorilere eklendi."}
    except Exception as e:
        if conn: conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn: conn.close()

@app.delete("/favorites/{user_id}/{movie_id}")
async def delete_favorite(user_id: int, movie_id: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "DELETE FROM favorites WHERE user_id = %s AND movie_id = %s",
            (user_id, movie_id)
        )
        conn.commit()
        return {"status": "success"}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

# api.py içine eklenecek kısım

@app.get("/favorites/{user_id}")
async def get_favorites(user_id: int):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Veritabanından o kullanıcıya ait tüm favorileri çekiyoruz
        query = """
            SELECT movie_id, title, genres, director, cast_members, poster_url, imdb_rating 
            FROM favorites 
            WHERE user_id = %s 
            ORDER BY created_at DESC
        """
        cursor.execute(query, (user_id,))
        rows = cursor.fetchall()
        
        # Verileri frontend'in beklediği MovieData formatına sokuyoruz
        favorites = [
            {
                "movie_id": r[0],
                "Film": r[1],          # Card.tsx 'Film' anahtarını bekliyor
                "Türler": r[2],        # Card.tsx 'Türler' anahtarını bekliyor
                "Director": r[3],
                "Cast": r[4],
                "Poster": r[5],
                "IMDb": r[6]
            } for r in rows
        ]
        
        return {"favorites": favorites}
    except Exception as e:
        print(f"❌ Favori getirme hatası: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            cursor.close()
            conn.close()

@app.get("/favorites/ids/{user_id}")
async def get_favorite_ids(user_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT movie_id FROM favorites WHERE user_id = %s", (user_id,))
    rows = cursor.fetchall()
    conn.close()
    return {"favorite_ids": [r[0] for r in rows]}

@app.post("/update-push-token")
async def update_push_token(req: TokenRequest):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET expo_push_token = %s WHERE id = %s", (req.token, req.user_id))
    conn.commit()
    conn.close()
    return {"status": "success"}

# 2. Ollama ile Bildirim Metni Üretme
async def generate_push_notification(user_id: int):
    # Kullanıcı personasını ve favorilerini al (Mevcut fonksiyonların)
    persona = get_user_persona(user_id)
    favorites = get_user_favorites(user_id)
    fav_titles = [f["Film"] for f in favorites["favorites"]][:5] # Sadece ilk 5 filmi al
    
    if not persona:
        return None

    system_prompt = """Sen heyecanlı ve samimi bir film danışmanısın. 
    Kullanıcının personasına ve favori filmlerine bakarak, ona izlemesi için *rastgele ve ilgi çekici* kısa bir bildirim mesajı (maksimum 150 karakter) yaz ve önerdiğin spesifik filmin tam adını belirt.
    
    Çıktıyı SADECE aşağıdaki JSON formatında ver, başka hiçbir metin veya açıklama ekleme:
    {
      "message": "En son Inception'ı sevmiştin, tam senin tarzına göre akıl bükücü bir film buldum: Shutter Island! Bakmak ister miydin?",
      "movie_title": "Shutter Island"
    }
    """
    
    user_msg = f"Persona: {persona}\nFavoriler: {', '.join(fav_titles)}"
    
    try:
        response = ollama.chat(
            model=os.getenv("OLLAMA_MODEL"), # Kullandığın model
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_msg}
            ]
        )
        content = response['message']['content'].strip()
        
        # Parse JSON
        import re
        import json
        clean_content = re.sub(r'```json\s?|```', '', content).strip()
        m = re.search(r'(\{.*\})', clean_content, re.DOTALL)
        data = json.loads(m.group(1)) if m else json.loads(clean_content)
        
        return data.get("message"), data.get("movie_title")
    except Exception as e:
        print(f"Ollama push notification generation error: {e}")
        return "Senin için yepyeni film önerilerim var, keşfetmek için dokun! 🍿", None

# 2.5. Bildirim için Cihaza Gönderilecek Öneri Filmleri TMDB'den Çekme
async def get_movies_for_push(user_id: int):
    try:
        # Get user favorites to find genres
        favorites_data = get_user_favorites(user_id)
        favs = favorites_data.get("favorites", [])
        
        # Collect user's favorite genres
        genre_ids = set()
        fav_titles = set()
        
        GENRE_DICT = {
            "Action": 28, "Adventure": 12, "Animation": 16, "Comedy": 35,
            "Crime": 80, "Documentary": 99, "Drama": 18, "Family": 10751,
            "Fantasy": 14, "History": 36, "Horror": 27, "Music": 10402,
            "Mystery": 9648, "Romance": 10749, "Science Fiction": 878,
            "TV Movie": 10770, "Thriller": 53, "War": 10752, "Western": 37
        }
        REVERSE_GENRE_DICT = {v: k for k, v in GENRE_DICT.items()}
        
        for f in favs:
            title = f.get("Film")
            if title:
                fav_titles.add(title.strip().lower())
            genres_str = f.get("Türler") or ""
            for g in genres_str.split(","):
                g = g.strip()
                if g in GENRE_DICT:
                    genre_ids.add(GENRE_DICT[g])
                    
        # Fallback to popular genres if none found
        if not genre_ids:
            genre_ids = {28, 12, 18, 878}  # Action, Adventure, Drama, Sci-Fi
            
        genre_str_params = ",".join(str(gid) for gid in genre_ids)
        
        # Discover movies from TMDB region TR
        discover_url = "https://api.themoviedb.org/3/discover/movie"
        params = {
            "include_adult": "false",
            "language": "tr-TR",
            "page": 1,
            "sort_by": "popularity.desc",
            "with_genres": genre_str_params,
            "vote_count.gte": 100
        }
        
        AUTH_KEY = os.getenv("AUTH_KEY")
        headers = {
            "accept": "application/json",
            "Authorization": f"Bearer {AUTH_KEY}"
        }
        
        resp = requests.get(discover_url, headers=headers, params=params)
        if resp.status_code != 200:
            print(f"TMDB discover error: {resp.status_code}")
            return []
            
        results = resp.json().get("results", [])
        recommended = []
        
        for m in results:
            title = m.get("title")
            if not title or title.strip().lower() in fav_titles:
                continue
                
            movie_id = m.get("id")
            overview = m.get("overview") or "Özet bulunamadı."
            poster_path = m.get("poster_path")
            rating = m.get("vote_average") or 0.0
            
            # Fetch credits
            credits_url = f"https://api.themoviedb.org/3/movie/{movie_id}/credits"
            credits_resp = requests.get(credits_url, headers=headers)
            director = "Bilinmiyor"
            cast = []
            if credits_resp.status_code == 200:
                crew = credits_resp.json().get("crew", [])
                for member in crew:
                    if member.get("job") == "Director":
                        director = member.get("name")
                        break
                cast_list = credits_resp.json().get("cast", [])[:3]
                cast = [c.get("name") for c in cast_list]
                
            cast_str = ", ".join(cast) if cast else "Bilinmiyor"
            
            # Fetch trailer
            videos_url = f"https://api.themoviedb.org/3/movie/{movie_id}/videos"
            videos_resp = requests.get(videos_url, headers=headers)
            trailer_url = None
            if videos_resp.status_code == 200:
                videos = videos_resp.json().get("results", [])
                for v in videos:
                    if v.get("site") == "YouTube" and v.get("type") == "Trailer":
                        trailer_url = f"https://www.youtube.com/watch?v={v.get('key')}"
                        break
            
            # Map genre IDs to names
            g_ids = m.get("genre_ids", [])
            g_names = [REVERSE_GENRE_DICT.get(gid, "Diğer") for gid in g_ids]
            genres_str = ", ".join(g_names)
            
            poster_url = f"https://image.tmdb.org/t/p/w500{poster_path}" if poster_path else "https://via.placeholder.com/500x750?text=No+Poster"
            
            movie_data = {
                "movie_id": str(movie_id),
                "Film": title,
                "Türler": genres_str,
                "Director": director,
                "Cast": cast_str,
                "Poster": poster_url,
                "IMDb": f"{rating:.1f}",
                "Özet": overview,
                "Fragman": trailer_url
            }
            recommended.append(movie_data)
            if len(recommended) >= 3:
                break
                
        return recommended
    except Exception as e:
        print(f"Error generating movies for push: {e}")
        return []

# 2.7. Belirli bir film başlığına göre TMDB'den kart oluşturma
async def get_movie_card_by_title(title: str):
    if not title:
        return None
    try:
        AUTH_KEY = os.getenv("AUTH_KEY")
        headers = {
            "accept": "application/json",
            "Authorization": f"Bearer {AUTH_KEY}"
        }
        
        # Search movie by title
        search_url = "https://api.themoviedb.org/3/search/movie"
        params = {"query": title, "language": "tr-TR"}
        resp = requests.get(search_url, headers=headers, params=params)
        
        if resp.status_code != 200:
            # Fallback to English search
            params["language"] = "en-US"
            resp = requests.get(search_url, headers=headers, params=params)
            
        results = resp.json().get("results", [])
        if not results:
            return None
            
        m = results[0]
        movie_id = m.get("id")
        title = m.get("title")
        overview = m.get("overview") or "Özet bulunamadı."
        poster_path = m.get("poster_path")
        rating = m.get("vote_average") or 0.0
        
        # Fetch credits
        credits_url = f"https://api.themoviedb.org/3/movie/{movie_id}/credits"
        credits_resp = requests.get(credits_url, headers=headers)
        director = "Bilinmiyor"
        cast = []
        if credits_resp.status_code == 200:
            crew = credits_resp.json().get("crew", [])
            for member in crew:
                if member.get("job") == "Director":
                    director = member.get("name")
                    break
            cast_list = credits_resp.json().get("cast", [])[:3]
            cast = [c.get("name") for c in cast_list]
            
        cast_str = ", ".join(cast) if cast else "Bilinmiyor"
        
        # Fetch trailer
        videos_url = f"https://api.themoviedb.org/3/movie/{movie_id}/videos"
        videos_resp = requests.get(videos_url, headers=headers)
        trailer_url = None
        if videos_resp.status_code == 200:
            videos = videos_resp.json().get("results", [])
            for v in videos:
                if v.get("site") == "YouTube" and v.get("type") == "Trailer":
                    trailer_url = f"https://www.youtube.com/watch?v={v.get('key')}"
                    break
        
        # Genre mapping
        GENRE_DICT = {
            "Action": 28, "Adventure": 12, "Animation": 16, "Comedy": 35,
            "Crime": 80, "Documentary": 99, "Drama": 18, "Family": 10751,
            "Fantasy": 14, "History": 36, "Horror": 27, "Music": 10402,
            "Mystery": 9648, "Romance": 10749, "Science Fiction": 878,
            "TV Movie": 10770, "Thriller": 53, "War": 10752, "Western": 37
        }
        REVERSE_GENRE_DICT = {v: k for k, v in GENRE_DICT.items()}
        
        g_ids = m.get("genre_ids", [])
        g_names = [REVERSE_GENRE_DICT.get(gid, "Diğer") for gid in g_ids]
        genres_str = ", ".join(g_names)
        
        poster_url = f"https://image.tmdb.org/t/p/w500{poster_path}" if poster_path else "https://via.placeholder.com/500x750?text=No+Poster"
        
        return {
            "movie_id": str(movie_id),
            "Film": title,
            "Türler": genres_str,
            "Director": director,
            "Cast": cast_str,
            "Poster": poster_url,
            "IMDb": f"{rating:.1f}",
            "Özet": overview,
            "Fragman": trailer_url
        }
    except Exception as e:
        print(f"Error fetching movie card for title {title}: {e}")
        return None

# 3. Rastgele Bildirim Gönderme Mantığı
async def send_random_notifications():
    conn = get_db_connection()
    cursor = conn.cursor()
    # Sadece push token'ı olan kullanıcıları al
    cursor.execute("SELECT id, expo_push_token FROM users WHERE expo_push_token IS NOT NULL")
    users = cursor.fetchall()
    conn.close()

    for user_id, push_token in users:
        # Rastgelelik kontrolü: Her çalışmada %20 ihtimalle bildirim gitsin
        if random.random() < 0.20:
            res = await generate_push_notification(user_id)
            if res:
                message_text, movie_title = res
                recommended_movies = []
                if movie_title:
                    movie_card = await get_movie_card_by_title(movie_title)
                    if movie_card:
                        recommended_movies = [movie_card]
                
                # If no specific movie card was found, fallback to general recommendations
                if not recommended_movies:
                    recommended_movies = await get_movies_for_push(user_id)

                try:
                    PushClient().publish(
                        PushMessage(
                            to=push_token, 
                            title="Senin İçin Bir Film Buldum 🍿", 
                            body=message_text,
                            data={
                                "type": "movie_recommendation",
                                "movies": recommended_movies
                            }
                        )
                    )
                    print(f"✅ Bildirim gönderildi: {user_id} | Önerilen Film: {movie_title}")
                except Exception as e:
                    print(f"❌ Bildirim hatası: {e}")



if __name__ == "__main__":
    uvicorn.run("api:app", host="0.0.0.0", port=3000, reload=True)