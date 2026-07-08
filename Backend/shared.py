# shared.py
from contextlib import AsyncExitStack
from typing import TypedDict, Annotated, List, Optional
import operator

class AppContext:
    def __init__(self):
        self.session = None
        self.tools = None
        self.exit_stack = AsyncExitStack()

# Ortak context objesi
ctx = AppContext()

# Ortak durum takibi için global sözlük
chat_statuses = {}

# Ortak tip tanımı
class GraphState(TypedDict):
    prompt: str
    persona: str
    intent: str
    messages: Annotated[List[dict], operator.add]
    final_output: str
    tools: List[dict]
    tool_calls: Optional[List[dict]]   # Hangi tool'lar çağrıldı, hangi argümanlarla
    tool_results: Optional[List[dict]] # MCP server'dan dönen ham veriler
    session_id: Optional[str]          # İstek oturum ID'si
