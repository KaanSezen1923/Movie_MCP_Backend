import streamlit as st
import requests
from server import GENRE_DICT

API_BASE_URL = "http://localhost:8000/api"

st.set_page_config(page_title="TMDB Film Gezgini", page_icon="🍿", layout="wide")

st.markdown("""
    <style>
    .movie-title { font-size: 28px; font-weight: 800; margin-bottom: 0px; padding-bottom: 0px; }
    .movie-year { font-size: 20px; font-weight: 400; color: #888; }
    .movie-genre { font-size: 15px; font-style: italic; color: #00ADB5; margin-top: -5px; margin-bottom: 10px;}
    .movie-credits { font-size: 15px; margin-bottom: 15px; padding: 10px; background-color: rgba(255,255,255,0.05); border-radius: 8px;}
    </style>
""", unsafe_allow_html=True)

st.title("🍿 TMDB Film Keşif Aracı")
st.markdown("Filtreleri kullanarak ruh haline en uygun filmi bul.")

with st.sidebar:
    st.header("🔍 Arama Filtreleri")
    
    genre_options = ["Farketmez"] + list(GENRE_DICT.keys())
    selected_genre = st.selectbox("Film Türü", genre_options)
    actor_name = st.text_input("Oyuncu (Örn: Christian Bale)")
    director_name = st.text_input("Yönetmen (Örn: Quentin Tarantino)")
    keyword = st.text_input("Anahtar Kelime (Örn: mafia, space)")
    min_rating = st.slider("Minimum TMDB Puanı", min_value=0.0, max_value=10.0, value=7.0, step=0.1)
    
    st.markdown("---")
    search_button = st.button("Filmleri Listele", type="primary", use_container_width=True)

if search_button:
    payload = {
        "genre_name": selected_genre if selected_genre != "Farketmez" else None,
        "actor_name": actor_name if actor_name.strip() else None,
        "director_name": director_name if director_name.strip() else None,
        "keyword": keyword if keyword.strip() else None,
        "min_rating": min_rating
    }
    
    with st.spinner("Sinema arşivi taranıyor..."):
        try:
            response = requests.post(f"{API_BASE_URL}/search", json=payload)
            response.raise_for_status() 
            
            api_data = response.json()
            movies_data = api_data.get("data", []) # JSON (List) alıyoruz
            error_message = None
            
        except requests.exceptions.RequestException as e:
            movies_data = []
            error_message = f"API Bağlantı Hatası: Lütfen backend'in çalıştığından emin olun. ({e})"

        st.subheader("📋 Sonuçlar")
        
        if error_message:
            st.error(error_message)
        elif not movies_data:
            st.warning("Aradığınız kriterlere uygun film bulunamadı.")
        else:
            for movie in movies_data:
                # --- JSON VERİSİNDEN DOĞRUDAN OKUMA ---
                title = movie.get("title", "Bilinmeyen Film")
                year = movie.get("year", "")
                rating = movie.get("rating", "0.0")
                genres = movie.get("genres", "Bilinmiyor")
                director_info = movie.get("director", "Bilinmiyor")
                cast_info = movie.get("cast", "Bilinmiyor")
                overview = movie.get("overview", "Özet bulunamadı.")
                platforms_list = movie.get("platforms", [])
                poster_url = movie.get("poster_url") or "https://via.placeholder.com/500x750?text=Poster+Bulunamadi"
                trailer_url = movie.get("trailer_url")

                with st.container():
                    col_poster, col_details = st.columns([1, 4])
                    
                    with col_poster:
                        st.image(poster_url, use_container_width=True)
                        
                    with col_details:
                        st.markdown(f'<p class="movie-title">{title} <span class="movie-year">({year})</span></p>', unsafe_allow_html=True)
                        st.markdown(f'<p class="movie-genre">{genres}</p>', unsafe_allow_html=True)
                        
                        st.markdown(
                            f'<div class="movie-credits">'
                            f'<b>🎬 Yönetmen:</b> {director_info} &nbsp;&nbsp;|&nbsp;&nbsp; '
                            f'<b>👥 Oyuncular:</b> {cast_info}'
                            f'</div>', 
                            unsafe_allow_html=True
                        )
                        
                        sub_col1, sub_col2 = st.columns([1, 3])
                        sub_col1.metric("⭐ TMDB Puanı", str(rating))
                        
                        with sub_col2:
                            if platforms_list:
                                st.success("📺 Platformlar: " + ", ".join(platforms_list))
                            else:
                                st.warning("🚫 Şu an popüler bir platformda yayında değil (Kiralama/Satın alma olabilir).")
                        
                        with st.expander("📝 Filmin Özeti", expanded=False):
                            st.write(overview)
                            
                        if trailer_url:
                            st.link_button("▶️ Fragmanı YouTube'da İzle", trailer_url)
                            
                st.divider()