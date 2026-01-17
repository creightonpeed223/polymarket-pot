"""
Advanced Sports News Monitor
Monitors multiple sports sources with league-specific detection and entity extraction
"""

import asyncio
import re
from datetime import datetime, timezone
from typing import List, Dict, Optional, Tuple
import httpx
from bs4 import BeautifulSoup
import xml.etree.ElementTree as ET

from .base import NewsMonitor, NewsEvent, EventType
from ..utils.logger import get_logger

logger = get_logger(__name__)


class SportsMonitor(NewsMonitor):
    """
    Advanced sports monitor with:
    - Multiple sources (ESPN, CBS Sports, Yahoo, Bleacher Report, RSS feeds)
    - League-specific detection (NFL, NBA, MLB, NHL, UFC/MMA, Soccer)
    - Entity extraction (players, teams, scores, contract values)
    - Injury severity classification
    """

    # ============================================
    # RSS Feeds (most reliable)
    # ============================================
    RSS_FEEDS = {
        "espn_top": {
            "url": "https://www.espn.com/espn/rss/news",
            "name": "ESPN Top",
            "leagues": ["general"],
        },
        "espn_nfl": {
            "url": "https://www.espn.com/espn/rss/nfl/news",
            "name": "ESPN NFL",
            "leagues": ["nfl"],
        },
        "espn_nba": {
            "url": "https://www.espn.com/espn/rss/nba/news",
            "name": "ESPN NBA",
            "leagues": ["nba"],
        },
        "espn_mlb": {
            "url": "https://www.espn.com/espn/rss/mlb/news",
            "name": "ESPN MLB",
            "leagues": ["mlb"],
        },
        "espn_nhl": {
            "url": "https://www.espn.com/espn/rss/nhl/news",
            "name": "ESPN NHL",
            "leagues": ["nhl"],
        },
        "cbssports_nfl": {
            "url": "https://www.cbssports.com/rss/headlines/nfl/",
            "name": "CBS NFL",
            "leagues": ["nfl"],
        },
        "cbssports_nba": {
            "url": "https://www.cbssports.com/rss/headlines/nba/",
            "name": "CBS NBA",
            "leagues": ["nba"],
        },
        "yahoo_nfl": {
            "url": "https://sports.yahoo.com/nfl/rss/",
            "name": "Yahoo NFL",
            "leagues": ["nfl"],
        },
        "bleacher_nfl": {
            "url": "https://bleacherreport.com/articles/feed?tag_id=16",
            "name": "Bleacher NFL",
            "leagues": ["nfl"],
        },
    }

    # ============================================
    # Web Sources (fallback)
    # ============================================
    WEB_SOURCES = {
        "espn_nfl": {
            "url": "https://www.espn.com/nfl/",
            "name": "ESPN NFL",
            "leagues": ["nfl"],
        },
        "espn_nba": {
            "url": "https://www.espn.com/nba/",
            "name": "ESPN NBA",
            "leagues": ["nba"],
        },
        "espn_mlb": {
            "url": "https://www.espn.com/mlb/",
            "name": "ESPN MLB",
            "leagues": ["mlb"],
        },
        "espn_nhl": {
            "url": "https://www.espn.com/nhl/",
            "name": "ESPN NHL",
            "leagues": ["nhl"],
        },
        "espn_mma": {
            "url": "https://www.espn.com/mma/",
            "name": "ESPN MMA",
            "leagues": ["ufc", "mma"],
        },
        "espn_soccer": {
            "url": "https://www.espn.com/soccer/",
            "name": "ESPN Soccer",
            "leagues": ["soccer"],
        },
    }

    # ============================================
    # League-specific team names
    # ============================================
    NFL_TEAMS = [
        "chiefs", "eagles", "49ers", "niners", "bills", "cowboys", "ravens", "bengals",
        "dolphins", "lions", "chargers", "jaguars", "vikings", "seahawks", "giants",
        "jets", "packers", "bears", "steelers", "browns", "raiders", "broncos",
        "saints", "buccaneers", "bucs", "falcons", "panthers", "cardinals", "rams",
        "commanders", "patriots", "titans", "colts", "texans",
    ]

    NBA_TEAMS = [
        "lakers", "celtics", "warriors", "nets", "bucks", "suns", "heat", "76ers",
        "sixers", "nuggets", "grizzlies", "cavaliers", "cavs", "mavericks", "mavs",
        "clippers", "hawks", "bulls", "knicks", "raptors", "jazz", "pelicans",
        "timberwolves", "wolves", "thunder", "blazers", "kings", "spurs", "rockets",
        "wizards", "pistons", "hornets", "magic", "pacers",
    ]

    MLB_TEAMS = [
        "yankees", "dodgers", "astros", "braves", "mets", "phillies", "padres",
        "mariners", "cardinals", "blue jays", "guardians", "rays", "orioles",
        "twins", "brewers", "giants", "red sox", "white sox", "cubs", "rangers",
        "diamondbacks", "d-backs", "reds", "tigers", "royals", "pirates", "rockies",
        "marlins", "angels", "athletics", "nationals",
    ]

    NHL_TEAMS = [
        "bruins", "avalanche", "hurricanes", "devils", "rangers", "maple leafs",
        "leafs", "oilers", "stars", "kings", "jets", "lightning", "panthers",
        "kraken", "wild", "flames", "islanders", "canucks", "senators", "penguins",
        "blues", "capitals", "caps", "predators", "red wings", "sabres", "ducks",
        "coyotes", "blackhawks", "flyers", "sharks", "golden knights", "knights",
        "canadiens", "habs", "blue jackets",
    ]

    UFC_FIGHTERS = [
        "jones", "adesanya", "izzy", "makhachev", "islam", "volkanovski", "volk",
        "edwards", "usman", "pereira", "poatan", "o'malley", "sean", "chimaev",
        "topuria", "pantoja", "grasso", "shevchenko", "nunes", "mcgregor", "conor",
        "diaz", "masvidal", "covington", "gaethje", "chandler", "poirier", "holloway",
        "strickland", "du plessis", "dricus", "ankalaev", "aspinall", "gane",
    ]

    # ============================================
    # Star players (high-impact news)
    # ============================================
    STAR_PLAYERS = {
        "nfl": [
            "mahomes", "allen", "burrow", "hurts", "lamar", "herbert", "tua",
            "rodgers", "prescott", "dak", "kelce", "travis", "hill", "tyreek",
            "chase", "jefferson", "diggs", "adams", "henry", "chubb", "mccaffrey",
            "donald", "parsons", "watt", "bosa", "garrett",
        ],
        "nba": [
            "lebron", "james", "curry", "steph", "durant", "kd", "giannis",
            "jokic", "embiid", "tatum", "luka", "doncic", "morant", "ja",
            "booker", "mitchell", "lillard", "dame", "kawhi", "leonard",
            "anthony", "edwards", "wembanyama", "wemby", "victor",
        ],
        "mlb": [
            "ohtani", "shohei", "trout", "judge", "soto", "acuna", "tatis",
            "betts", "freeman", "turner", "alvarez", "devers", "harper",
            "machado", "arenado", "vlad", "guerrero", "rodriguez", "julio",
        ],
        "nhl": [
            "mcdavid", "connor", "mackinnon", "draisaitl", "makar", "kaprizov",
            "matthews", "marner", "ovechkin", "ovi", "crosby", "sid", "kucherov",
            "pastrnak", "panarin", "bedard", "connor",
        ],
    }

    # ============================================
    # Injury keywords by severity
    # ============================================
    INJURY_SEVERE = [
        "torn acl", "torn mcl", "torn achilles", "broken", "fracture", "fractured",
        "surgery", "season-ending", "out for season", "out for year", "career",
        "ligament tear", "ruptured", "dislocated",
    ]

    INJURY_MODERATE = [
        "sprain", "strain", "concussion", "hamstring", "groin", "calf",
        "ankle", "knee", "shoulder", "out 4-6", "out 6-8", "miss several",
        "week-to-week", "extended absence",
    ]

    INJURY_MINOR = [
        "day-to-day", "questionable", "doubtful", "probable", "limited",
        "resting", "load management", "minor", "precautionary",
    ]

    # ============================================
    # Trade/Transaction keywords
    # ============================================
    TRADE_KEYWORDS = [
        "trade", "traded", "trading", "signs", "signed", "signing",
        "free agent", "contract", "extension", "released", "waived",
        "acquired", "deal", "agreement", "terms", "restructure",
        "franchise tag", "tagged", "opt out", "opt-out",
    ]

    # Contract value patterns
    CONTRACT_PATTERN = re.compile(r'\$(\d+(?:\.\d+)?)\s*(million|m|billion|b)', re.I)
    YEARS_PATTERN = re.compile(r'(\d+)[\s-]?year', re.I)

    # ============================================
    # Result keywords
    # ============================================
    RESULT_KEYWORDS = [
        "wins", "win", "won", "defeats", "defeated", "beat", "beats",
        "loses", "lost", "eliminated", "advances", "clinches",
        "champion", "championship", "title", "playoff", "finals",
        "super bowl", "world series", "stanley cup", "nba finals",
        "knockout", "ko", "tko", "submission", "decision", "upset",
    ]

    # Score patterns
    SCORE_PATTERN = re.compile(r'(\d{1,3})\s*[-–]\s*(\d{1,3})')

    def __init__(self, check_interval: int = 45):
        super().__init__("Sports", check_interval)
        self._headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }

    async def check(self) -> List[NewsEvent]:
        """Check all sports sources"""
        events = []

        async with httpx.AsyncClient(timeout=20.0) as client:
            # Check RSS feeds first (more reliable)
            for feed_id, feed_info in self.RSS_FEEDS.items():
                try:
                    feed_events = await self._check_rss_feed(
                        client,
                        feed_info["url"],
                        feed_info["name"],
                        feed_info["leagues"],
                    )
                    events.extend(feed_events)
                except Exception as e:
                    logger.debug(f"RSS feed {feed_id} failed: {e}")

            # Check web sources
            for source_id, source_info in self.WEB_SOURCES.items():
                try:
                    source_events = await self._check_web_source(
                        client,
                        source_info["url"],
                        source_info["name"],
                        source_info["leagues"],
                    )
                    events.extend(source_events)
                except Exception as e:
                    logger.debug(f"Web source {source_id} failed: {e}")

        if events:
            logger.info(f"Sports monitor found {len(events)} events")

        return events

    async def _check_rss_feed(
        self,
        client: httpx.AsyncClient,
        url: str,
        source_name: str,
        leagues: List[str],
    ) -> List[NewsEvent]:
        """Check an RSS feed for sports news"""
        events = []

        try:
            response = await client.get(url, headers=self._headers, follow_redirects=True)
            response.raise_for_status()

            root = ET.fromstring(response.content)
            items = []

            # RSS 2.0
            for item in root.findall(".//item"):
                title_elem = item.find("title")
                link_elem = item.find("link")
                if title_elem is not None and title_elem.text:
                    items.append({
                        "title": title_elem.text.strip(),
                        "url": link_elem.text.strip() if link_elem is not None and link_elem.text else url,
                    })

            # Atom
            for entry in root.findall(".//{http://www.w3.org/2005/Atom}entry"):
                title_elem = entry.find("{http://www.w3.org/2005/Atom}title")
                link_elem = entry.find("{http://www.w3.org/2005/Atom}link")
                if title_elem is not None and title_elem.text:
                    items.append({
                        "title": title_elem.text.strip(),
                        "url": link_elem.get("href") if link_elem is not None else url,
                    })

            for item in items[:15]:
                event = self._process_headline(item["title"], item["url"], source_name, leagues)
                if event:
                    events.append(event)

        except Exception as e:
            logger.debug(f"RSS check failed for {source_name}: {e}")

        return events

    async def _check_web_source(
        self,
        client: httpx.AsyncClient,
        url: str,
        source_name: str,
        leagues: List[str],
    ) -> List[NewsEvent]:
        """Check a web source for sports news"""
        events = []

        try:
            response = await client.get(url, headers=self._headers, follow_redirects=True)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")
            headlines = []

            # Find headlines
            for selector in [
                "h1 a", "h2 a", ".contentItem__title",
                ".headlineStack__list a", ".news-feed-item a",
                "article h1", "article h2", ".Story__Title",
            ]:
                for elem in soup.select(selector)[:12]:
                    text = elem.get_text(strip=True)
                    href = elem.get("href", "")
                    if text and len(text) > 15:
                        if href and not href.startswith("http"):
                            href = f"https://www.espn.com{href}"
                        headlines.append({"title": text, "url": href or url})

            # Deduplicate
            seen = set()
            for h in headlines:
                key = h["title"].lower()[:50]
                if key not in seen:
                    seen.add(key)
                    event = self._process_headline(h["title"], h["url"], source_name, leagues)
                    if event:
                        events.append(event)

        except Exception as e:
            logger.debug(f"Web check failed for {source_name}: {e}")

        return events

    def _process_headline(
        self,
        title: str,
        article_url: str,
        source_name: str,
        source_leagues: List[str],
    ) -> Optional[NewsEvent]:
        """Process a headline and create an event if relevant"""

        # Create unique ID
        item_id = f"sports_{source_name}_{hash(title)}"
        if not self._is_new(item_id):
            return None

        title_lower = title.lower()

        # Classify event type and extract data
        event_type, keywords = self._classify_event(title_lower)
        if not event_type:
            return None

        # Detect league
        league = self._detect_league(title_lower, source_leagues)

        # Extract entities
        teams = self._extract_teams(title_lower, league)
        players = self._extract_players(title_lower, league)

        # Extract additional data based on event type
        extra_data = {}

        if event_type == EventType.SPORTS_INJURY:
            extra_data["severity"] = self._classify_injury_severity(title_lower)

        if event_type == EventType.SPORTS_TRADE:
            contract = self._extract_contract_value(title)
            if contract:
                extra_data["contract"] = contract

        if event_type == EventType.SPORTS_RESULT:
            score = self._extract_score(title)
            if score:
                extra_data["score"] = score

        # Determine outcome and confidence
        outcome, confidence = self._extract_outcome(title_lower, event_type)

        # Build entities list
        entities = []
        if players:
            entities.extend([p.title() for p in players[:3]])
        if teams:
            entities.extend([t.title() for t in teams[:2]])

        # Add league and extra data to keywords
        all_keywords = keywords + [league] if league else keywords
        if extra_data:
            for k, v in extra_data.items():
                all_keywords.append(f"{k}:{v}")

        # Boost confidence for star players
        is_star = any(p in title_lower for p in self._get_all_star_players())
        if is_star:
            confidence = min(confidence + 0.15, 0.99)

        event = NewsEvent(
            event_type=event_type,
            headline=title[:200],
            content=title,
            source_url=article_url,
            source_name=source_name,
            keywords=all_keywords[:8],
            entities=entities,
            outcome=outcome,
            confidence=confidence,
        )

        logger.info(f"SPORTS [{league or 'general'}]: {title[:60]}")
        return event

    def _classify_event(self, headline_lower: str) -> Tuple[Optional[EventType], List[str]]:
        """Classify the type of sports event"""
        keywords = []

        # Check for injuries (highest priority for trading)
        for kw in self.INJURY_SEVERE + self.INJURY_MODERATE + self.INJURY_MINOR:
            if kw in headline_lower:
                keywords.append(kw.replace(" ", "_"))
        if keywords:
            return EventType.SPORTS_INJURY, keywords[:3]

        # Check for trades/transactions
        keywords = []
        for kw in self.TRADE_KEYWORDS:
            if kw in headline_lower:
                keywords.append(kw)
        if keywords:
            return EventType.SPORTS_TRADE, keywords[:3]

        # Check for results
        keywords = []
        for kw in self.RESULT_KEYWORDS:
            if kw in headline_lower:
                keywords.append(kw)
        if keywords:
            return EventType.SPORTS_RESULT, keywords[:3]

        # General sports news if contains team/player names
        if self._has_sports_entity(headline_lower):
            return EventType.SPORTS_NEWS, ["sports"]

        return None, []

    def _detect_league(self, headline_lower: str, source_leagues: List[str]) -> Optional[str]:
        """Detect which league the news is about"""
        # Explicit league mentions
        if any(x in headline_lower for x in ["nfl", "football", "super bowl", "touchdown"]):
            return "nfl"
        if any(x in headline_lower for x in ["nba", "basketball"]):
            return "nba"
        if any(x in headline_lower for x in ["mlb", "baseball", "world series"]):
            return "mlb"
        if any(x in headline_lower for x in ["nhl", "hockey", "stanley cup"]):
            return "nhl"
        if any(x in headline_lower for x in ["ufc", "mma", "knockout", "submission"]):
            return "ufc"
        if any(x in headline_lower for x in ["soccer", "premier league", "champions league", "la liga"]):
            return "soccer"

        # Check by team names
        if any(t in headline_lower for t in self.NFL_TEAMS):
            return "nfl"
        if any(t in headline_lower for t in self.NBA_TEAMS):
            return "nba"
        if any(t in headline_lower for t in self.MLB_TEAMS):
            return "mlb"
        if any(t in headline_lower for t in self.NHL_TEAMS):
            return "nhl"
        if any(f in headline_lower for f in self.UFC_FIGHTERS):
            return "ufc"

        # Default to source league
        if source_leagues and source_leagues[0] != "general":
            return source_leagues[0]

        return None

    def _extract_teams(self, headline_lower: str, league: Optional[str]) -> List[str]:
        """Extract team names from headline"""
        teams = []

        team_lists = {
            "nfl": self.NFL_TEAMS,
            "nba": self.NBA_TEAMS,
            "mlb": self.MLB_TEAMS,
            "nhl": self.NHL_TEAMS,
        }

        if league and league in team_lists:
            for team in team_lists[league]:
                if team in headline_lower:
                    teams.append(team)
        else:
            # Check all leagues
            for team_list in team_lists.values():
                for team in team_list:
                    if team in headline_lower:
                        teams.append(team)

        return list(set(teams))[:3]

    def _extract_players(self, headline_lower: str, league: Optional[str]) -> List[str]:
        """Extract player names from headline"""
        players = []

        if league and league in self.STAR_PLAYERS:
            for player in self.STAR_PLAYERS[league]:
                if player in headline_lower:
                    players.append(player)
        else:
            # Check all leagues
            for player_list in self.STAR_PLAYERS.values():
                for player in player_list:
                    if player in headline_lower:
                        players.append(player)

        # Check UFC fighters
        for fighter in self.UFC_FIGHTERS:
            if fighter in headline_lower:
                players.append(fighter)

        return list(set(players))[:3]

    def _classify_injury_severity(self, headline_lower: str) -> str:
        """Classify injury severity"""
        for kw in self.INJURY_SEVERE:
            if kw in headline_lower:
                return "severe"
        for kw in self.INJURY_MODERATE:
            if kw in headline_lower:
                return "moderate"
        return "minor"

    def _extract_contract_value(self, headline: str) -> Optional[str]:
        """Extract contract value from headline"""
        match = self.CONTRACT_PATTERN.search(headline)
        if match:
            value = float(match.group(1))
            unit = match.group(2).lower()
            if unit in ["b", "billion"]:
                return f"${value}B"
            return f"${value}M"

        years_match = self.YEARS_PATTERN.search(headline)
        if years_match:
            return f"{years_match.group(1)}-year"

        return None

    def _extract_score(self, headline: str) -> Optional[str]:
        """Extract game score from headline"""
        match = self.SCORE_PATTERN.search(headline)
        if match:
            return f"{match.group(1)}-{match.group(2)}"
        return None

    def _extract_outcome(self, headline_lower: str, event_type: EventType) -> Tuple[str, float]:
        """Extract outcome and confidence"""

        if event_type == EventType.SPORTS_RESULT:
            if any(w in headline_lower for w in ["wins", "win", "won", "defeats", "beat", "beats"]):
                return "WIN", 0.95
            if any(w in headline_lower for w in ["loses", "lost", "defeated"]):
                return "LOSS", 0.95
            if "champion" in headline_lower:
                return "CHAMPION", 0.98
            if "eliminated" in headline_lower:
                return "ELIMINATED", 0.95

        if event_type == EventType.SPORTS_INJURY:
            severity = self._classify_injury_severity(headline_lower)
            if severity == "severe":
                return "OUT_LONG_TERM", 0.9
            if severity == "moderate":
                return "OUT_WEEKS", 0.8
            return "QUESTIONABLE", 0.7

        if event_type == EventType.SPORTS_TRADE:
            if any(w in headline_lower for w in ["signs", "signed", "agrees"]):
                return "SIGNED", 0.9
            if any(w in headline_lower for w in ["traded", "acquired", "trade"]):
                return "TRADED", 0.9
            if any(w in headline_lower for w in ["released", "waived", "cut"]):
                return "RELEASED", 0.9

        return "UNKNOWN", 0.5

    def _has_sports_entity(self, headline_lower: str) -> bool:
        """Check if headline contains any sports entity"""
        all_teams = self.NFL_TEAMS + self.NBA_TEAMS + self.MLB_TEAMS + self.NHL_TEAMS
        all_players = self._get_all_star_players()

        return (
            any(t in headline_lower for t in all_teams) or
            any(p in headline_lower for p in all_players) or
            any(f in headline_lower for f in self.UFC_FIGHTERS)
        )

    def _get_all_star_players(self) -> List[str]:
        """Get all star players across leagues"""
        players = []
        for player_list in self.STAR_PLAYERS.values():
            players.extend(player_list)
        return players
