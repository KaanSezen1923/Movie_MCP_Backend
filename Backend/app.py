import streamlit as st
import re
from server import search_movies_by_filters, GENRE_DICT
import requests

API_BASE_URL = "http://localhost:8000/api"

# Sayfa Yapılandırması
st.set_page_config(page_title="TMDB Film Gezgini", page_icon="🍿", layout="wide")

# Daha şık bir görünüm için özel CSS tanımlamaları (Yönetmen/Cast için yeni stil eklendi)
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

# Yan Menü (Filtreler)
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
            # FastAPI'ye POST isteği atıyoruz
            response = requests.post(f"{API_BASE_URL}/search", json=payload)
            response.raise_for_status()  # Hata kodlarını (4xx, 5xx) yakalar
            
            api_data = response.json()
            results_text = api_data.get("data", "Sonuç alınamadı.")
            
        except requests.exceptions.RequestException as e:
            results_text = f"API Bağlantı Hatası: Lütfen backend'in çalıştığından emin olun. ({e})"

        
        st.subheader("📋 Sonuçlar")
        
        if "Aradığınız kriterlere uygun film bulunamadı" in results_text or "Hata oluştu" in results_text:
            st.error(results_text)
        else:
            movies = results_text.split("-" * 30)
            
            for movie_text in movies:
                if not movie_text.strip():
                    continue
                    
                # --- METİN PARÇALAMA (REGEX) İŞLEMİ ---
                poster_match = re.search(r"🖼️ Poster:\s*(http[^\n]+)", movie_text)
                poster_url = poster_match.group(1) if poster_match else "https://via.placeholder.com/500x750?text=Poster+Bulunamadi"
                
                title_match = re.search(r"🎬\s*(.+?)\s*\((\d{4}|\?\?\?\?)\)", movie_text)
                title = title_match.group(1).strip() if title_match else "Bilinmeyen Film"
                year = title_match.group(2) if title_match else ""
                
                rating_match = re.search(r"⭐ Puan:\s*([\d.]+)", movie_text)
                rating = rating_match.group(1) if rating_match else "0.0"
                
                genres_match = re.search(r"🎭 Türler:\s*([^\n]+)", movie_text)
                genres = genres_match.group(1) if genres_match else ""
                
                # YENİ: Yönetmen ve Oyuncu verilerini ayıklama
                director_match = re.search(r"👤 Yönetmen:\s*(.*?)\s*\|", movie_text)
                director_info = director_match.group(1).strip() if director_match else "Bilinmiyor"
                
                cast_match = re.search(r"👥 Oyuncular:\s*([^\n]+)", movie_text)
                cast_info = cast_match.group(1).strip() if cast_match else "Bilinmiyor"
                
                overview_match = re.search(r"📝 Özet:\s*(.*?)\n📺 Platformlar:", movie_text, re.DOTALL)
                overview = overview_match.group(1).strip() if overview_match else "Özet bulunamadı."
                
                platforms_match = re.search(r"📺 Platformlar:\s*([^\n]+)", movie_text)
                platforms = platforms_match.group(1).replace("📺 İzleyebileceğin Platformlar: ", "") if platforms_match else "Veri yok"
                
                trailer_match = re.search(r"🔗\s*(https://www.youtube.com[^n]+)", movie_text)
                trailer_url = trailer_match.group(1) if trailer_match else None

                # --- MODERN UI GÖSTERİMİ ---
                with st.container():
                    col_poster, col_details = st.columns([1, 4])
                    
                    with col_poster:
                        st.image(poster_url, use_container_width=True)
                        
                    with col_details:
                        st.markdown(f'<p class="movie-title">{title} <span class="movie-year">({year})</span></p>', unsafe_allow_html=True)
                        st.markdown(f'<p class="movie-genre">{genres}</p>', unsafe_allow_html=True)
                        
                        # YENİ: Yönetmen ve Cast bilgisi özel CSS ile kutu içinde gösteriliyor
                        st.markdown(
                            f'<div class="movie-credits">'
                            f'<b>🎬 Yönetmen:</b> {director_info} &nbsp;&nbsp;|&nbsp;&nbsp; '
                            f'<b>👥 Oyuncular:</b> {cast_info}'
                            f'</div>', 
                            unsafe_allow_html=True
                        )
                        
                        sub_col1, sub_col2 = st.columns([1, 3])
                        sub_col1.metric("⭐ TMDB Puanı", rating)
                        with sub_col2:
                            if "Şu an popüler bir platformda yayında değil" in platforms:
                                st.warning("🚫 " + platforms)
                            else:
                                st.success("📺 Platformlar: " + platforms)
                        
                        with st.expander("📝 Filmin Özeti", expanded=False):
                            st.write(overview)
                            
                        if trailer_url:
                            st.link_button("▶️ Fragmanı YouTube'da İzle", trailer_url)
                            
                st.divider()