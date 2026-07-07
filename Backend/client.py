import json
import re
import os
from pydantic import BaseModel
from shared import ctx, GraphState
import ollama
from typing import TypedDict, Annotated, List
from langgraph.graph import StateGraph, END
import operator
from dotenv import load_dotenv

load_dotenv()

MODEL_NAME = os.getenv("OLLAMA_MODEL", "gpt-oss:120b-cloud")

class GraphState(TypedDict):
    prompt: str
    persona: str
    intent: str
    messages: Annotated[List[dict], operator.add]
    final_output: str
    tools: List[dict] # API'den gelen tool tanımları
    tool_calls: list   # Çağrılan tool'ların adı + argümanları
    tool_results: list # MCP server'd an dönen ham veriler

async def analyze_intent_node(state: GraphState):
    model_name = MODEL_NAME
    intent_instructions = """
    Sen bir Film Botu Yönlendiricisisin. 
    'recommendation': Film önerisi, detay sorma veya duygusal/mod durumları (üzgünüm, canım sıkkın vb.).
    'general': Sadece selamlaşma.
    Cevap: SADECE kategori adı.
    """
    response = ollama.chat(
        model=model_name,
        messages=[{'role': 'system', 'content': intent_instructions}, {'role': 'user', 'content': state['prompt']}]
    )
    intent = response['message']['content'].strip().lower()
    return {"intent": "recommendation" if "recommendation" in intent else "general"}

async def recommendation_node(state: GraphState):
    """
    Gelişmiş film öneri düğümü.

    Akış:
      1. Reasoning Katmanı  — kullanıcı mesajından niyet, duygu, tür, kişi, mood çıkar.
      2. Agentic Döngü      — maksimum MAX_TURNS turda modeli tool'larla dolaştır.
                              Her turda model ya tool çağırır ya da final JSON üretir.
      3. Fallback Stratejisi — boş sonuç ya da tool çağrılmadıysa argümanları genişleterek
                              ikinci bir arama dene.
      4. Kalite Kontrolü    — parse edilen JSON'da film listesi boşsa kullanıcıya
                              anlamlı bir mesaj döndür.
    """
    model_name = MODEL_NAME
    MAX_TURNS   = 3   # Agentic döngü üst sınırı

    # ── 1. REASONING KATMANI ──────────────────────────────────────────────────
    # Modelden önce kullanıcı isteğini derinlemesine analiz et.
    # Bu adım hem argüman kalitesini artırır hem de
    # "canım sıkkın", "heyecanlı bir şeyler" gibi mood ifadelerini
    # somut arama parametrelerine dönüştürür.
    reasoning_system = f"""
Sen bir film öneri motorunun analiz bileşenisin.
Kullanıcı mesajını ve sohbet geçmişini inceleyerek aşağıdaki JSON şemasını doldur.
Çıktı SADECE JSON olacak, başka açıklama olmayacak.

Kullanıcı Personası: {state['persona']}

Şema:
{{
  "mood": "kullanıcının ruh hali (örn: üzgün, heyecanlı, nostaljik, keyifli, ...)",
  "genre": "en uygun TMDB türü — sadece şunlardan biri seç: Action, Adventure, Animation, Comedy, Crime, Documentary, Drama, Family, Fantasy, History, Horror, Music, Mystery, Romance, Science Fiction, Thriller, War, Western — veya null",
  "director_name": "açıkça belirtilmişse yönetmen adı, yoksa null",
  "actor_name": "açıkça belirtilmişse oyuncu adı, yoksa null",
  "keyword": "temayı özetleyen 1-2 kelimelik anahtar (örn: space, revenge, time travel), yoksa null",
  "reference_movie": "kullanıcının açıkça beğendiğini/izlediğini belirttiği ve 'buna benzer' dediği spesifik bir film adı varsa buraya yaz (örn: 'Açlık Oyunları çok beğendim, benzer öner' -> 'The Hunger Games'), film adını İngilizce/orijinal adıyla yaz, yoksa null",
  "min_rating": "minimum IMDb puanı 0.0-10.0 arasında float, belirtilmemişse 6.5",
  "avoid": "kullanıcının istemediği tür veya konu varsa buraya yaz, yoksa null",
  "reasoning": "kararlarının kısa gerekçesi (1 cümle)"
}}
"""
    reasoning_resp = ollama.chat(
        model=model_name,
        messages=[
            {'role': 'system', 'content': reasoning_system},
            {'role': 'user',   'content': state['prompt']},
        ]
    )
    raw_reasoning = reasoning_resp['message']['content']

    # Reasoning JSON'unu ayrıştır — hata olursa varsayılanlarla devam et
    try:
        clean_r = re.sub(r'```json\s?|```', '', raw_reasoning).strip()
        m = re.search(r'(\{.*\})', clean_r, re.DOTALL)
        intent_data: dict = json.loads(m.group(1)) if m else {}
    except Exception:
        intent_data = {}

    # ── 2. ANA SYSTEM MESAJI ─────────────────────────────────────────────────
    mood_hint       = intent_data.get('mood', '')
    avoid_hint      = intent_data.get('avoid', '')
    reasoning_txt   = intent_data.get('reasoning', '')
    reference_movie = intent_data.get('reference_movie', '')

    avoid_clause     = f"\n    Kaçın: {avoid_hint}" if avoid_hint else ""
    mood_clause      = f"\n    Kullanıcı şu an '{mood_hint}' hissediyor; önerilerini buna göre tonu ayarla." if mood_hint else ""
    reference_clause = f"\n    Referans Film: Kullanıcı '{reference_movie}' filmini beğendiğini belirtti; bu filme benzer öneriler istiyor." if reference_movie else ""

    system_msg = f"""
Sen deneyimli, empatik bir film danışmanısın.

Kullanıcı Personası  : {state['persona']}
Anlık Ruh Hali Analizi: {mood_hint or 'belirtilmedi'}
Reasoning            : {reasoning_txt or '-'}{mood_clause}{avoid_clause}{reference_clause}

GÖREV:
- Kullanıcı belirli bir filmi beğendiğini söyleyip ona BENZER film istiyorsa (örn: "X'i çok beğendim, benzer öner"),
  MUTLAKA 'get_similar_movies' aracını referans film adıyla kullan. Bu durumda 'search_movies_by_filters' KULLANMA,
  çünkü tür/anahtar kelime bazlı arama spesifik bir filme olan benzerliği yakalayamaz.
- Kullanıcı tür, oyuncu, yönetmen veya genel bir mood/tema belirtiyorsa (spesifik bir referans film YOKSA)
  'search_movies_by_filters' aracını MUTLAKA kullan.
- Araç boş sonuç dönerse farklı parametrelerle tekrar dene (keyword veya genre değiştir).
- Kullanıcıya hitap ederken ruh haline uygun, samimi ve kişiselleştirilmiş bir ton seç.

NİHAİ CEVAP FORMATI — SADECE JSON, başka hiçbir metin ya da markdown olmayacak:
{{
  "type": "movie_list",
  "text": "Kullanıcıya yönelik samimi, kişiselleştirilmiş açıklama",
  "mood_response": "Kullanıcının ruh haline özel 1 cümlelik empati notu",
  "movies": [
    {{
      "Film"                  : "Film Adı",
      "Director"              : "Yönetmen",
      "Cast"                  : "Başroller",
      "Yıl"                   : "2024",
      "IMDb"                  : "8.5",
      "Türler"                : "Tür(ler)",
      "Özet"                  : "Kısa özet",
      "Poster"                : "https://... veya URL",
      "Fragman"               : "https://... veya URL",
      "Şu Anki Platform(lar)" : "Netflix / Amazon vb.",
      "Neden Önerildi"        : "Bu filme özel 1 cümlelik kişiselleştirilmiş gerekçe"
    }}
  ]
}}
"""

    # ── 3. AGENTIC DÖNGÜ ─────────────────────────────────────────────────────
    current_messages = [{'role': 'system', 'content': system_msg}] + state['messages']
    tool_calls_log   = []
    tool_results_log = []
    raw_content      = ''
    tool_was_called  = False

    for turn in range(MAX_TURNS):
        response = ollama.chat(
            model=model_name,
            messages=current_messages,
            tools=state['tools'],
            options={"num_predict": 2048, "temperature": 0.7}
        )
        message = response.get('message', {})

        if not message.get('tool_calls'):
            # Model artık tool çağırmıyor — cevabı al ve döngüyü kır
            raw_content = message.get('content', '')
            break

        # Tool çağrıları var — işle
        tool_was_called = True
        current_messages.append(message)

        for tool_call in message['tool_calls']:
            tool_name = tool_call['function']['name']
            tool_args = tool_call['function']['arguments']

            # MCP aracını çalıştır
            result         = await ctx.session.call_tool(tool_name, tool_args)
            raw_result_text = result.content[0].text

            # Logla
            tool_calls_log.append({"tool_name": tool_name, "arguments": tool_args})
            tool_results_log.append({"tool_name": tool_name, "raw_result": raw_result_text})

            # Sonucu geçmişe ekle
            current_messages.append({
                'role':    'tool',
                'content': raw_result_text,
                'name':    tool_name,
            })

        # Son turda model hala tool çağırıyorsa, döngü biter ve
        # bir sonraki iterasyonda son yanıtı almaya çalışır.

    # ── 4. FALLBACK: Tool Hiç Çağrılmadıysa ────────────────────────────────
    # Model arama yapmadan cevap verdiyse (tool_was_called=False),
    # reasoning'den çıkardığımız parametrelerle manuel bir tool çağrısı yap.
    if not tool_was_called and state['tools']:
        ref_movie = intent_data.get('reference_movie')

        if ref_movie:
            # Kullanıcı spesifik bir filme benzer öneri istiyor
            tool_name     = 'get_similar_movies'
            fallback_args = {
                "movie_title": ref_movie,
                "min_rating":  intent_data.get('min_rating') or 0.0,
            }
        else:
            tool_name     = 'search_movies_by_filters'
            fallback_args = {
                k: v for k, v in {
                    "genre_name":    intent_data.get('genre'),
                    "actor_name":    intent_data.get('actor_name'),
                    "director_name": intent_data.get('director_name'),
                    "keyword":       intent_data.get('keyword'),
                    "min_rating":    intent_data.get('min_rating', 6.5),
                }.items() if v is not None
            }

        if fallback_args:
            try:
                result          = await ctx.session.call_tool(tool_name, fallback_args)
                raw_result_text = result.content[0].text
                tool_calls_log.append({"tool_name": tool_name, "arguments": fallback_args})
                tool_results_log.append({"tool_name": tool_name, "raw_result": raw_result_text})

                # Sonuçla birlikte tekrar nihai yanıt üret
                current_messages.append({
                    'role': 'tool', 'content': raw_result_text, 'name': tool_name
                })
                final_resp  = ollama.chat(
                    model=model_name,
                    messages=current_messages,
                    options={"num_predict": 2048, "temperature": 0.7}
                )
                raw_content = final_resp['message']['content']
            except Exception:
                pass  # Fallback başarısız olursa mevcut raw_content ile devam et

    # ── 5. JSON PARSE & KALİTE KONTROLÜ ─────────────────────────────────────
    def _build_fallback(text: str) -> dict:
        return {
            "type":          "movie_list",
            "mood_response": "",
            "text":          text or "Üzgünüm, uygun bir film bulamadım.",
            "movies":        [],
        }

    try:
        clean_json_str = re.sub(r'```json\s?|```', '', raw_content).strip()
        m = re.search(r'(\{.*\})', clean_json_str, re.DOTALL)
        if m:
            clean_json_str = m.group(1)

        parsed_data = json.loads(clean_json_str)

        # Film listesi boşsa kullanıcıya anlamlı mesaj ver
        if not parsed_data.get('movies'):
            parsed_data['text'] = (
                parsed_data.get('text') or
                "Aradığın kriterlere uygun film bulunamadı. "
                "Farklı bir tür veya oyuncu denemek ister misin?"
            )

        return {
            "final_output": json.dumps(parsed_data, ensure_ascii=False),
            "tool_calls":   tool_calls_log,
            "tool_results": tool_results_log,
        }

    except (json.JSONDecodeError, AttributeError):
        return {
            "final_output": json.dumps(_build_fallback(raw_content), ensure_ascii=False),
            "tool_calls":   tool_calls_log,
            "tool_results": tool_results_log,
        }

   

async def general_chat_node(state: GraphState):
    response = ollama.chat(model=MODEL_NAME, messages=state['messages'])
    return {"final_output": response['message']['content']}

# --- Grafik Kurulumu ---
def route_by_intent(state: GraphState):
    return "recommendation_engine" if state["intent"] == "recommendation" else "general_chatter"

workflow = StateGraph(GraphState)
workflow.add_node("intent_analyzer", analyze_intent_node)
workflow.add_node("recommendation_engine", recommendation_node)
workflow.add_node("general_chatter", general_chat_node)

workflow.set_entry_point("intent_analyzer")
workflow.add_conditional_edges("intent_analyzer", route_by_intent)
workflow.add_edge("recommendation_engine", END)
workflow.add_edge("general_chatter", END)

app = workflow.compile()

async def get_ollama_tools(session):
    """Sunucudaki tool'ları Ollama formatına çevirir"""
    mcp_tools = await session.list_tools()
    return [
        {
            'type': 'function',
            'function': {
                'name': tool.name,
                'description': tool.description,
                'parameters': tool.inputSchema,
            },
        }
        for tool in mcp_tools.tools
    ]

async def generate_user_profile(chat_history_text, favorites_text):
    """Sohbet geçmişi ve favorileri birleştirerek kullanıcı personası oluşturur."""
    model_name = MODEL_NAME
    
    profiler_instructions = f"""
    Sen uzman bir kullanıcı deneyimi analistisin. 
    Sana verilen "Favori Listesi" (kullanıcının açıkça beğendiğini belirttikleri) ve 
    "Sohbet Geçmişi" (kullanıcının doğal etkileşimleri) verilerini birleştirerek derinlikli bir Persona Özeti oluştur.

    Analizinde şu hiyerarşiyi izle:
    1. Temel İlgi Alanları: Favori listesindeki film, yönetmen ve türler.
    2. Davranışsal Analiz: Sohbet geçmişinden anlaşılan güncel ruh hali ve tercih değişimleri.
    3. Kaçınılanlar: Sevmediği veya ilgilenmediği belirtilen içerikler.
    4. İletişim Tonu: Kullanıcının dil kullanımı (resmi, samimi, kısa, detaycı).

    Çıktı Kuralları:
    - Üçüncü şahıs ağzından yaz.
    - Maksimum 3-4 cümle ile net bir profil çiz.
    - "Kullanıcı..." diye başla.
    """

    user_input = f"""
    KULLANICI FAVORİLERİ:
    {favorites_text}

    SOHBET GEÇMİŞİ:
    {chat_history_text}
    """

    response = ollama.chat(
        model=model_name,
        messages=[
            {'role': 'system', 'content': profiler_instructions},
            {'role': 'user', 'content': user_input}
        ]
    )
    return response['message']['content']