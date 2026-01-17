  """Player Statistics Module"""
  from datetime import datetime, timezone, timedelta
  from typing import Dict, Optional
  from dataclasses import dataclass

  @dataclass
  class PlayerStats:
      name: str
      team: str
      league: str
      position: str
      impact_score: float = 0.0
      recent_trend: str = "stable"
      updated_at: Optional[datetime] = None

  class PlayerStatsService:
      KNOWN_PLAYERS = {
          "lebron": 90, "curry": 88, "durant": 87, "giannis": 92, "jokic": 93,
          "embiid": 88, "tatum": 85, "luka": 90, "morant": 82, "edwards": 84,
          "wembanyama": 80, "mahomes": 95, "allen": 90, "burrow": 88, "hurts": 85,
          "kelce": 88, "hill": 86, "jefferson": 88, "mccaffrey": 90, "henry": 85,
          "ohtani": 95, "judge": 88, "trout": 85, "mcdavid": 95, "crosby": 85,
      }
      def __init__(self):
          self._cache = {}
      async def get_player_stats(self, name: str, league: str = None) -> Optional[PlayerStats]:
          key = name.lower()
          if key in self.KNOWN_PLAYERS:
              return PlayerStats(name=name, team="", league=league or "", position="", impact_score=self.KNOWN_PLAYERS[key])
          return None
      def get_edge_adjustment(self, stats: Optional[PlayerStats], event_type: str) -> float:
          if not stats: return 0.0
          if stats.impact_score >= 90: return 0.20
          elif stats.impact_score >= 80: return 0.15
          elif stats.impact_score >= 70: return 0.10
          elif stats.impact_score >= 60: return 0.05
          return 0.0

  _stats_service = None
  def get_stats_service():
      global _stats_service
      if _stats_service is None:
          _stats_service = PlayerStatsService()
      return _stats_service
  ENDFILE
ls -la player_stats.py
