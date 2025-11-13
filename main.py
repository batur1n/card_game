# main.py
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import json
import uuid
import asyncio
from typing import Dict, List, Optional
from enum import Enum
import random
import logging
from datetime import datetime

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        # logging.FileHandler('game_debug.log'),  # Disabled to save server space
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

app = FastAPI()

# Serve static files
app.mount("/static", StaticFiles(directory="static"), name="static")

def get_rank_symbol(rank: int) -> str:
    """Convert card rank to display symbol"""
    rank_map = {
        6: '6', 7: '7', 8: '8', 9: '9', 10: '10',
        11: 'J', 12: 'Q', 13: 'K', 14: 'A'
    }
    return rank_map.get(rank, str(rank))

class GamePhase(Enum):
    WAITING = "waiting"
    PHASE_ONE = "phase_one"
    DONATION = "donation"
    PHASE_TWO = "phase_two"
    FINISHED = "finished"

class Card:
    def __init__(self, suit: str, rank: int):
        self.suit = suit  # 'hearts', 'diamonds', 'clubs', 'spades'
        self.rank = rank  # 6-14 (6-10, J=11, Q=12, K=13, A=14)
        logger.info(f"Card created: {self.suit} {self.rank}")
        
    def __dict__(self):
        return {"suit": self.suit, "rank": self.rank}
    
    def to_dict(self):
        result = {"suit": self.suit, "rank": self.rank}
        logger.info(f"Card.to_dict() returning: {result}")
        return result

class Player:
    def __init__(self, username: str, websocket: WebSocket):
        self.id = str(uuid.uuid4())
        self.username = username
        self.websocket = websocket
        self.ready = False
        self.hand = []
        self.visible_stack = []
        self.hidden_cards = []
        # bad_card_counter removed - using bad_card_players list in GameRoom instead
        self.is_out = False
        self.has_drawn_this_turn = False
        self.cards_played_this_turn = 0
        self.has_donated = False  # Add this line
        self.pending_donations = {}  # Add this line
        self.last_played_card = None  # Track last card played in phase 2
        self.has_picked_hidden_cards = False  # Track if hidden cards were picked up
        self.is_loser = False  # Track if player is the loser (for displaying clown emoji)
        self.loss_count = 0  # Track cumulative losses for penalty calculation

class GameRoom:
    def __init__(self, room_id: str):
        self.id = room_id
        self.players: List[Player] = []
        self.phase = GamePhase.WAITING
        self.deck = []
        self.current_player_index = 0
        self.trump_suit = None
        self.last_card_player = None  # For showing last drawn card to other players (cleared after placement)
        self.last_deck_card_player = None  # Player who drew the LAST card from deck (for Phase 2 first turn)
        self.battle_pile = []
        self.last_drawn_card = None
        self.losers_from_previous_game = []  # Track losers for turn order
        self.drawn_cards_order = []  # Track order of drawn cards for trump determination
        self.pile_discard_in_progress = False  # Flag for 3-second delay when showing beaten pile
        self.bad_card_players = []  # List of dicts: [{"player_id": str, "reason": str}] in order they got penalties
        self.current_donation_index = 0  # Current position in bad_card_players list during donation phase
        
    def add_player(self, player: Player):
        if len(self.players) < 6:
            self.players.append(player)
            return True
        return False
    
    def remove_player(self, player_id: str):
        self.players = [p for p in self.players if p.id != player_id]
    
    def get_player_by_id(self, player_id: str) -> Optional[Player]:
        return next((p for p in self.players if p.id == player_id), None)
    
    def create_deck(self):
        """Create standard 36 card deck (6-A in all suits)"""
        suits = ['hearts', 'diamonds', 'clubs', 'spades']
        self.deck = []
        for suit in suits:
            for rank in range(6, 15):  # 6-14 (A=14)
                card = Card(suit, rank)
                self.deck.append(card)
                if suit == 'hearts':  # Log hearts to see ranks
                    logger.info(f"create_deck: Created {suit} card with rank {rank}")
        logger.info(f"create_deck: Total deck size: {len(self.deck)}")
        random.shuffle(self.deck)
    
    def deal_initial_cards(self):
        """Deal hidden + 1 visible card to each player (losers get extra hidden cards)"""
        for player in self.players:
            # Base: 2 hidden cards + cumulative loss count
            num_hidden_cards = 2 + player.loss_count
            
            # Deal hidden cards
            player.hidden_cards = [self.deck.pop() for _ in range(num_hidden_cards)]
            # 1 visible card
            player.visible_stack = [self.deck.pop()]
    
    def can_stack_card(self, card: Card, target_stack: List[Card]) -> bool:
        """Check if card can be stacked on target (rank + 1 or 6 on Ace)"""
        if not target_stack:
            return True
        
        top_card = target_stack[-1]
        logger.info(f"can_stack_card: Trying to place {card.rank} on {top_card.rank}")
        
        # Normal stacking rule: card rank = top card rank + 1
        if card.rank == top_card.rank + 1:
            logger.info(f"can_stack_card: Normal seniority rule applies ({card.rank} on {top_card.rank})")
            return True
        
        # Special rule: 6 on Ace
        if card.rank == 6 and top_card.rank == 14:
            logger.info(f"can_stack_card: Special 6 on Ace rule applies")
            return True
        
        logger.info(f"can_stack_card: No seniority rule applies")
        return False
    
    def can_give_top_stack_card(self, player: Player) -> bool:
        """Check if player's top stack card can be given to any other player"""
        if not player.visible_stack or len(player.visible_stack) < 2:
            return False
        
        top_card = player.visible_stack[-1]
        card_underneath = player.visible_stack[-2]
        
        # Check if top card has seniority with card underneath
        # If yes, player is allowed to keep it (no penalty for drawing)
        if self.can_stack_card(top_card, [card_underneath]):
            logger.info(f"can_give_top_stack_card: Top card {top_card.rank} has seniority with card underneath {card_underneath.rank}, no penalty")
            return False
        
        # Check all other players
        for other_player in self.players:
            if other_player.id == player.id:
                continue
            
            if self.can_stack_card(top_card, other_player.visible_stack):
                return True
        
        return False
    
    def get_valid_moves_for_player(self, player: Player) -> List[dict]:
        """Get all valid moves a player can make with their current hand"""
        valid_moves = []
        
        if not player.hand:
            return valid_moves
        
        # Check if player can place cards from their stack to others
        if len(player.visible_stack) > 1:  # Has more than just the base card
            top_card = player.visible_stack[-1]
            for target_player in self.players:
                if target_player.id != player.id:
                    if self.can_stack_card(top_card, target_player.visible_stack):
                        valid_moves.append({
                            'type': 'give_from_stack',
                            'card': top_card.to_dict(),
                            'target_player_id': target_player.id
                        })
        
        # Check if drawn card can be placed on other players
        if player.hand:
            for card in player.hand:
                for target_player in self.players:
                    if self.can_stack_card(card, target_player.visible_stack):
                        valid_moves.append({
                            'type': 'place_drawn_card',
                            'card': card.to_dict(),
                            'target_player_id': target_player.id
                        })
        
        return valid_moves
    
    def determine_trump_suit(self):
        """Determine trump suit from drawn cards order (last non-spade card)"""
        # Go through drawn cards in reverse order to find last non-spade
        for card in reversed(self.drawn_cards_order):
            if card.suit != 'spades':
                self.trump_suit = card.suit
                return
        
        # If all drawn cards were spades, trump is spades
        self.trump_suit = 'spades'
    
    def determine_first_player(self):
        """Determine who goes first based on previous game losers or random"""
        if self.losers_from_previous_game:
            # Find the first loser who is still in the game
            for loser_id in self.losers_from_previous_game:
                for i, player in enumerate(self.players):
                    if player.id == loser_id:
                        self.current_player_index = i
                        logger.info(f"First player determined: {player.username} (was loser from previous game)")
                        return
        
        # If no previous losers or they're not in game, random selection
        self.current_player_index = random.randint(0, len(self.players) - 1)
        logger.info(f"First player random: {self.players[self.current_player_index].username}")
    
    def all_players_ready(self) -> bool:
        return len(self.players) >= 2 and all(p.ready for p in self.players)

# Global game state
rooms: Dict[str, GameRoom] = {}
connections: Dict[str, WebSocket] = {}
# Track unique IPs with last connection timestamp and connection count
connected_ips: Dict[str, Dict[str, any]] = {}  # {ip: {"timestamp": str, "count": int}}

def save_ip_to_file(ip: str, timestamp: str, count: int):
    """Save IP address with timestamp and connection count to file, updating if already exists"""
    try:
        # Read existing IPs
        ip_data = {}
        try:
            with open('connected_ips.txt', 'r') as f:
                for line in f:
                    if '|' in line:
                        parts = line.strip().split('|')
                        if len(parts) == 3:
                            stored_ip, stored_time, stored_count = parts
                            ip_data[stored_ip] = {"timestamp": stored_time, "count": int(stored_count)}
                        elif len(parts) == 2:
                            # Backwards compatibility: old format without count
                            stored_ip, stored_time = parts
                            ip_data[stored_ip] = {"timestamp": stored_time, "count": 1}
        except FileNotFoundError:
            pass
        
        # Update with new/updated IP
        ip_data[ip] = {"timestamp": timestamp, "count": count}
        
        # Write back to file
        with open('connected_ips.txt', 'w') as f:
            for stored_ip, data in sorted(ip_data.items()):
                f.write(f"{stored_ip}|{data['timestamp']}|{data['count']}\n")
    except Exception as e:
        logger.error(f"Failed to save IP to file: {e}")

class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
    
    async def connect(self, websocket: WebSocket, client_id: str):
        await websocket.accept()
        self.active_connections[client_id] = websocket
    
    def disconnect(self, client_id: str):
        if client_id in self.active_connections:
            del self.active_connections[client_id]
    
    async def send_personal_message(self, message: str, client_id: str):
        if client_id in self.active_connections:
            await self.active_connections[client_id].send_text(message)
    
    async def broadcast_to_room(self, message: str, room_id: str):
        if room_id in rooms:
            for player in rooms[room_id].players:
                if player.id in self.active_connections:
                    try:
                        await self.active_connections[player.id].send_text(message)
                    except:
                        pass

manager = ConnectionManager()

@app.get("/")
async def read_index():
    return FileResponse('static/index.html')

@app.get("/api/rooms")
async def get_rooms():
    room_list = []
    for room_id, room in rooms.items():
        if room.phase == GamePhase.WAITING:
            room_list.append({
                "id": room_id,
                "players": len(room.players),
                "max_players": 6
            })
    return {"rooms": room_list}

@app.post("/api/rooms")
async def create_room():
    room_id = str(uuid.uuid4())[:8]
    rooms[room_id] = GameRoom(room_id)
    return {"room_id": room_id}

@app.get("/api/stats")
async def get_stats():
    """Get server statistics including unique IP count"""
    return {
        "unique_ips": len(connected_ips),
        "active_rooms": len(rooms),
        "total_players": sum(len(room.players) for room in rooms.values())
    }

@app.websocket("/ws/{room_id}/{username}")
async def websocket_endpoint(websocket: WebSocket, room_id: str, username: str):
    # Log unique IP address with timestamp and increment connection count
    client_ip = websocket.client.host if websocket.client else "unknown"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    if client_ip not in connected_ips:
        # New IP
        connected_ips[client_ip] = {"timestamp": timestamp, "count": 1}
        logger.info(f"New unique IP connected: {client_ip} (Total unique IPs: {len(connected_ips)})")
    else:
        # Existing IP - increment counter
        connected_ips[client_ip]["count"] += 1
        connected_ips[client_ip]["timestamp"] = timestamp
        logger.info(f"IP reconnected: {client_ip} (Connection #{connected_ips[client_ip]['count']})")
    
    save_ip_to_file(client_ip, timestamp, connected_ips[client_ip]["count"])
    
    player = Player(username, websocket)
    
    # Create room if it doesn't exist
    if room_id not in rooms:
        rooms[room_id] = GameRoom(room_id)
    
    room = rooms[room_id]
    
    # Check if player is joining mid-game or during waiting phase
    is_new_player = player.id not in [p.id for p in room.players]
    is_waiting_phase = room.phase == GamePhase.WAITING
    
    # Block new players from joining during active game
    if is_new_player and not is_waiting_phase:
        await websocket.close(code=1008, reason="Game in progress. Please wait for the current round to finish.")
        return
    
    if not room.add_player(player):
        await websocket.close(code=1000, reason="Room full")
        return
    
    # If new player joins during waiting phase (after a game), reset loser penalties
    if is_new_player and is_waiting_phase and hasattr(room, 'losers_from_previous_game') and room.losers_from_previous_game:
        room.losers_from_previous_game = []
        logger.info(f"New player {username} joined during waiting - loser penalties reset")
        await manager.broadcast_to_room(
            room_id,
            json.dumps({"type": "notification", "message": "New player joined! Hidden card penalties have been reset."})
        )
    
    await manager.connect(websocket, player.id)
    
    # Send initial game state
    await send_game_state(room)
    
    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            
            await handle_message(room, player, message)
            
    except WebSocketDisconnect:
        room.remove_player(player.id)
        manager.disconnect(player.id)
        if room.players:
            await send_game_state(room)
        elif room_id in rooms and not room.players:
            del rooms[room_id]

async def handle_message(room: GameRoom, player: Player, message: dict):
    action = message.get("action")
    
    if action == "ready":
        logger.info(f"Player {player.username} marked as ready in phase {room.phase}")
        player.ready = True
        ready_count = sum(1 for p in room.players if p.ready)
        logger.info(f"Ready count: {ready_count}/{len(room.players)}, min players: 2, all_players_ready: {room.all_players_ready()}")
        if room.all_players_ready():
            logger.info(f"All players ready! Starting game...")
            await start_game(room)
        else:
            logger.info(f"Not all players ready yet, sending game state")
            await send_game_state(room)
    
    elif action == "draw_card" and room.phase == GamePhase.PHASE_ONE:
        result = await handle_draw_card(room, player)
        await send_game_state(room)
    
    elif action == "place_card" and room.phase == GamePhase.PHASE_ONE:
        await handle_place_card(room, player, message)
    
    elif action == "give_from_stack" and room.phase == GamePhase.PHASE_ONE:
        await handle_give_from_stack(room, player, message)
    
    elif action == "play_card" and room.phase == GamePhase.PHASE_TWO:
        await handle_play_card(room, player, message)
    
    elif action == "beat_card" and room.phase == GamePhase.PHASE_TWO:
        await handle_beat_card(room, player, message)
    
    elif action == "take_pile" and room.phase == GamePhase.PHASE_TWO:
        await handle_take_pile(room, player)
    
    elif action == "end_turn" and room.phase == GamePhase.PHASE_ONE:
        # Remove manual end turn - turns should end automatically when placing on own stack
        # Only allow if player hasn't drawn yet and must draw
        player_obj = room.get_player_by_id(player.id)
        if (room.players[room.current_player_index].id == player.id and 
            not player_obj.hand and not room.deck):
            await end_player_turn(room, player)
    
    elif action == "donate_cards" and room.phase == GamePhase.DONATION:
        await handle_donate_cards(room, player, message)

async def start_game(room: GameRoom):
    room.phase = GamePhase.PHASE_ONE
    room.battle_pile = []
    room.discarded_cards = []
    room.trump_suit = None
    room.drawn_cards_order = []
    room.last_drawn_card = None
    room.last_card_player = None
    room.last_deck_card_player = None
    room.current_player_index = 0
    
    # Clear donation tracker if it exists
    if hasattr(room, 'donation_tracker'):
        room.donation_tracker = {}
    
    # Reset all player states
    for player in room.players:
        player.ready = False  # Reset ready status for next round
        player.has_drawn_this_turn = False
        player.cards_played_this_turn = 0
        player.hand = []
        player.visible_stack = []
        player.hidden_cards = []
        # bad_card_counter removed - using room.bad_card_players list instead
        player.is_out = False
        player.has_picked_hidden_cards = False
        player.last_played_card = None
        player.has_donated = False
        player.pending_donations = {}
        # Note: is_loser flag is NOT reset here - it stays through the next game
        if hasattr(player, 'locked_stack_cards'):
            player.locked_stack_cards.clear()
    
    # Create deck and deal cards (with loser penalties if any)
    room.create_deck()
    room.deal_initial_cards()  # This handles losers_from_previous_game
    room.determine_first_player()
    
    # Clear losers list after using it for first player determination and penalty cards
    # This ensures next game will only track the NEW loser
    room.losers_from_previous_game = []
    
    await send_game_state(room)

async def handle_draw_card(room: GameRoom, player: Player):
    """Handle drawing a card from deck"""
    if room.players[room.current_player_index].id != player.id:
        return {"error": "Not your turn"}
    
    if not room.deck:
        return {"error": "Deck is empty"}
    
    if player.hand:  # Player already has a card in hand
        return {"error": "You already have a card in hand"}
    
    # PENALTY RULE 1: Check if top stack card could be given to others before drawing
    # Only applies if: stack has >= 2 cards AND top card has NO seniority with card underneath
    if len(player.visible_stack) >= 2:
        top_card = player.visible_stack[-1]
        underneath_card = player.visible_stack[-2]
        
        # Check if top card has seniority with underneath card
        has_seniority_with_underneath = (
            top_card.rank == underneath_card.rank + 1 or
            (top_card.rank == 6 and underneath_card.rank == 14)
        )
        
        # Only check penalty if top card does NOT have seniority with underneath
        if not has_seniority_with_underneath:
            # Check if top card could have been given to another player
            can_give_top_to_someone = False
            target_card = None
            for target_player in room.players:
                if target_player.id != player.id:
                    if room.can_stack_card(top_card, target_player.visible_stack):
                        can_give_top_to_someone = True
                        target_card = target_player.visible_stack[-1] if target_player.visible_stack else None
                        break
            
            if can_give_top_to_someone:
                reason = f"{get_rank_symbol(top_card.rank)} → {get_rank_symbol(target_card.rank)}" if target_card else f"{get_rank_symbol(top_card.rank)} → пусто"
                room.bad_card_players.append({"player_id": player.id, "reason": reason})
                logger.info(f"{player.username} got bad card: drew from deck while top stack card could have been given to others")
    
    # Draw card from deck
    drawn_card = room.deck.pop()
    logger.info(f"handle_draw_card: Drew card from deck - rank {drawn_card.rank}, suit {drawn_card.suit}")
    
    player.hand = [drawn_card]  # Player can only have one card in hand
    room.last_drawn_card = drawn_card
    room.last_card_player = player  # Track who drew this card (for notification visibility)
    room.drawn_cards_order.append(drawn_card)  # Track order for trump determination
    
    logger.info(f"handle_draw_card: Player {player.username} now has in hand: {[(c.rank, c.suit) for c in player.hand]}")
    
    # Check if this was the last card - determine trump and track player for Phase 2 first turn
    if not room.deck:
        room.determine_trump_suit()
        room.last_deck_card_player = player  # This player goes first in Phase 2
    
    return {"success": True, "drawn_card": drawn_card.to_dict()}

async def handle_place_card(room: GameRoom, player: Player, message: dict):
    """Handle placing a card from hand to a player's stack"""
    if room.players[room.current_player_index].id != player.id:
        return
    
    card_data = message.get("card")
    target_player_id = message.get("target_player_id")
    
    if not card_data or not target_player_id:
        return
    
    # Find the card in player's hand
    card_to_place = None
    for hand_card in player.hand:
        if hand_card.suit == card_data["suit"] and hand_card.rank == card_data["rank"]:
            card_to_place = hand_card
            break
    
    if not card_to_place:
        return
    
    # Find target player
    target_player = room.get_player_by_id(target_player_id)
    if not target_player:
        return
    
    # Check if placing on own stack or another player's stack
    placing_on_own_stack = (target_player_id == player.id)
    
    if not placing_on_own_stack:
        # Placing on another player's stack - check seniority rule
        if not room.can_stack_card(card_to_place, target_player.visible_stack):
            # PENALTY RULE 4: Invalid move - add player to bad card list
            # Card stays on target player's stack where it was placed
            target_card = target_player.visible_stack[-1] if target_player.visible_stack else None
            reason = f"{get_rank_symbol(card_to_place.rank)} ✗ {get_rank_symbol(target_card.rank)}" if target_card else f"{get_rank_symbol(card_to_place.rank)} ✗ пусто"
            room.bad_card_players.append({"player_id": player.id, "reason": reason})
            target_player.visible_stack.append(card_to_place)
            player.hand.remove(card_to_place)
            
            # Clear last drawn card (card has been placed)
            room.last_drawn_card = None
            room.last_card_player = None
            
            await manager.send_personal_message(
                json.dumps({"type": "error", "message": "Invalid card placement! Bad card counter increased."}), 
                player.id
            )
            await end_player_turn(room, player)
            return
        
        # PENALTY RULE 3: Check for 6 on Ace penalty (only receiver gets penalty)
        if (card_to_place.rank == 6 and target_player.visible_stack and 
            target_player.visible_stack[-1].rank == 14):
            reason = "6 → A"
            room.bad_card_players.append({"player_id": target_player.id, "reason": reason})
            await manager.send_personal_message(
                json.dumps({"type": "notification", "message": "You received 6 on Ace! Added to bad card list."}), 
                target_player.id
            )
        
        # Place the card on another player's stack
        target_player.visible_stack.append(card_to_place)
        player.hand.remove(card_to_place)
        
        # Clear last drawn card (card has been placed)
        room.last_drawn_card = None
        room.last_card_player = None
        
        # Check if deck is empty - transition to donation phase
        if not room.deck:
            await transition_to_donation_phase(room)
        else:
            # Player can continue - they can draw again if deck exists
            await send_game_state(room)

    else:
    # Placing on own stack
        if room.can_stack_card(card_to_place, player.visible_stack):
            # Check 6 on Ace
            if (card_to_place.rank == 6 and player.visible_stack and 
                player.visible_stack[-1].rank == 14):
                reason = "6 → A"
                room.bad_card_players.append({"player_id": player.id, "reason": reason})
            # Turn continues when seniority rule applies on your own stack
            player.visible_stack.append(card_to_place)
            player.hand.remove(card_to_place)
            
            # Clear last drawn card (card has been placed)
            room.last_drawn_card = None
            room.last_card_player = None
            
            # Check if deck is empty after placing - transition to donation phase
            if not room.deck:
                await transition_to_donation_phase(room)
            else:
                await send_game_state(room)
        else:
            # Placing on own stack WITHOUT seniority rule
            # PENALTY RULE 2: Check if drawn card could have been given to others
            can_give_drawn_to_someone = False
            target_card = None
            for target_player in room.players:
                if target_player.id != player.id:
                    if room.can_stack_card(card_to_place, target_player.visible_stack):
                        can_give_drawn_to_someone = True
                        target_card = target_player.visible_stack[-1] if target_player.visible_stack else None
                        break
            
            if can_give_drawn_to_someone:
                reason = f"{get_rank_symbol(card_to_place.rank)} → {get_rank_symbol(target_card.rank)}" if target_card else f"{get_rank_symbol(card_to_place.rank)} → пусто"
                room.bad_card_players.append({"player_id": player.id, "reason": reason})
                logger.info(f"{player.username} got bad card: could give drawn card to others but placed on own stack without seniority")
            
            # Turn ends automatically when placing on own stack and no seniority rule
            player.visible_stack.append(card_to_place)
            player.hand.remove(card_to_place)
            
            # Clear last drawn card (card has been placed)
            room.last_drawn_card = None
            room.last_card_player = None
            
            await end_player_turn(room, player)
            return

async def handle_give_from_stack(room: GameRoom, player: Player, message: dict):
    """Handle giving card from player's own stack to another player"""
    if room.players[room.current_player_index].id != player.id:
        return
    
    target_player_id = message.get("target_player_id")
    
    if len(player.visible_stack) <= 1:
        return  # Can't give if only has base card
    
    target_player = room.get_player_by_id(target_player_id)
    if not target_player:
        return
    
    # Get top card from player's stack
    top_card = player.visible_stack[-1]
    
    # Check if this card is locked (player drew from deck instead of giving it)
    if hasattr(player, 'locked_stack_cards'):
        if (top_card.suit, top_card.rank) in player.locked_stack_cards:
            await manager.send_personal_message(
                json.dumps({"type": "error", "message": "This card is locked! You should have given it before drawing."}), 
                player.id
            )
            return
    
    # Check if move is valid
    if not room.can_stack_card(top_card, target_player.visible_stack):
        # PENALTY RULE 4: Invalid move - card stays on target player's stack, turn ends
        target_card = target_player.visible_stack[-1] if target_player.visible_stack else None
        reason = f"{get_rank_symbol(top_card.rank)} ✗ {get_rank_symbol(target_card.rank)}" if target_card else f"{get_rank_symbol(top_card.rank)} ✗ пусто"
        room.bad_card_players.append({"player_id": player.id, "reason": reason})
        
        # Move the card to target player's stack even though it's invalid
        card = player.visible_stack.pop()
        target_player.visible_stack.append(card)
        
        await manager.send_personal_message(
            json.dumps({"type": "error", "message": "Invalid card placement from stack! Card moved to target, turn ends, bad card added."}), 
            player.id
        )
        await end_player_turn(room, player)
        return
    
    # PENALTY RULE 3: Check for 6 on Ace penalty (only receiver gets penalty)
    if (top_card.rank == 6 and target_player.visible_stack and 
        target_player.visible_stack[-1].rank == 14):
        reason = "6 → A"
        room.bad_card_players.append({"player_id": target_player.id, "reason": reason})
        await manager.send_personal_message(
            json.dumps({"type": "notification", "message": "You received 6 on Ace! Added to bad card list."}), 
            target_player.id
        )
    
    # Move the card
    card = player.visible_stack.pop()
    target_player.visible_stack.append(card)
    
    # Check if deck is empty - transition to donation phase
    if not room.deck:
        await transition_to_donation_phase(room)
    else:
        # Player continues their turn - they can give more from stack or draw from deck
        # Don't end turn automatically, let player decide next action
        await send_game_state(room)

async def end_player_turn(room: GameRoom, player: Player):
    """End current player's turn and move to next player"""
    # Reset turn flags
    player.has_drawn_this_turn = False
    player.cards_played_this_turn = 0
    
    # If player still has cards in hand, put them on their own stack
    while player.hand:
        card = player.hand.pop()
        player.visible_stack.append(card)
    
    # Move to next active player (skip players who are out)
    starting_index = room.current_player_index
    while True:
        room.current_player_index = (room.current_player_index + 1) % len(room.players)
        next_player = room.players[room.current_player_index]
        
        # If we've cycled through all players and they're all out, game is over
        if room.current_player_index == starting_index:
            # Check if all players are out (shouldn't happen, but safety check)
            active_players = [p for p in room.players if not p.is_out]
            if len(active_players) == 0:
                # Everyone is out - game over (shouldn't reach here)
                room.phase = GamePhase.WAITING
                await send_game_state(room)
                return
            # If we're back at starting index and that player is not out, continue
            if not room.players[starting_index].is_out:
                break
        
        # Found an active player
        if not next_player.is_out:
            break
    
    # Check if deck is empty - transition to donation phase
    if not room.deck:
        await transition_to_donation_phase(room)
    else:
        await send_game_state(room)

def can_beat_card(card: Card, target_card: Card, trump_suit: str) -> bool:
    """Check if card can beat target card in battle"""
    # 7 of spades beats everything
    if card.suit == 'spades' and card.rank == 7:
        return True
    
    # Can't beat 7 of spades unless with another spade
    if target_card.suit == 'spades' and target_card.rank == 7:
        return card.suit == 'spades'
    
    # Same suit: higher rank or 6 beats Ace (works for all suits including spades)
    if card.suit == target_card.suit:
        if card.rank > target_card.rank:
            return True
        if card.rank == 6 and target_card.rank == 14:
            return True
        return False
    
    # Spades can only be beaten by spades (already handled above in same suit check)
    if target_card.suit == 'spades':
        return False
    
    # Trump beats non-trump (unless target is spade)
    if card.suit == trump_suit and target_card.suit != trump_suit:
        return True
    
    return False

async def handle_play_card(room: GameRoom, player: Player, message: dict):
    """Handle playing a card to start or continue battle pile"""
    if room.players[room.current_player_index].id != player.id:
        await manager.send_personal_message(
            json.dumps({"type": "error", "message": "Not your turn"}),
            player.id
        )
        return
    
    if player.is_out:
        return
    
    card_data = message.get("card")
    if not card_data:
        return
    
    # Find card in player's hand
    card_to_play = None
    for hand_card in player.hand:
        if hand_card.suit == card_data["suit"] and hand_card.rank == card_data["rank"]:
            card_to_play = hand_card
            break
    
    if not card_to_play:
        await manager.send_personal_message(
            json.dumps({"type": "error", "message": "Card not in hand"}),
            player.id
        )
        return
    
    # If battle pile is empty, just add the card
    if not room.battle_pile:
        player.hand.remove(card_to_play)
        room.battle_pile.append(card_to_play)
        player.last_played_card = card_to_play
        logger.info(f"{player.username} started battle pile with {card_to_play.rank} of {card_to_play.suit}")
        
        # Check if player should pick hidden cards or win
        await check_player_status(room, player)
        
        # Check if game ended (phase changed to WAITING)
        if room.phase == GamePhase.WAITING:
            # Game is over, send final state and don't advance turn
            await send_game_state(room)
            return
        
        # Move to next player
        room.current_player_index = (room.current_player_index + 1) % len(room.players)
        # Skip players who are out
        while room.players[room.current_player_index].is_out:
            room.current_player_index = (room.current_player_index + 1) % len(room.players)
        
        await send_game_state(room)
        return
    
    # Battle pile exists - must beat the top card
    top_card = room.battle_pile[-1]
    
    if not can_beat_card(card_to_play, top_card, room.trump_suit):
        await manager.send_personal_message(
            json.dumps({"type": "error", "message": "Card cannot beat the top card"}),
            player.id
        )
        return
    
    # Successfully beat the card
    player.hand.remove(card_to_play)
    room.battle_pile.append(card_to_play)
    player.last_played_card = card_to_play
    logger.info(f"{player.username} beat with {card_to_play.rank} of {card_to_play.suit}")
    
    # Check if pile should be discarded (size == number of active players)
    active_players = [p for p in room.players if not p.is_out]
    
    if len(room.battle_pile) >= len(active_players):
        logger.info(f"Battle pile complete ({len(room.battle_pile)} cards) - showing for 3 seconds before discarding")
        
        # Set flag to hide "take pile" button during delay
        room.pile_discard_in_progress = True
        
        # First, send game state with the complete pile so all players can see it
        await send_game_state(room)
        
        # Wait 3 seconds so all players can see the winning card
        await asyncio.sleep(3)
        
        # Clear flag after delay
        room.pile_discard_in_progress = False
        
        logger.info(f"Battle pile discarded ({len(room.battle_pile)} cards)")
        
        # Move cards to discarded pile
        if not hasattr(room, 'discarded_cards'):
            room.discarded_cards = []
        room.discarded_cards.extend(room.battle_pile)
        room.battle_pile = []
        
        # Check ALL active players' status after discard
        # After pile is discarded, check who has cards left
        # The player with cards LEFT is the loser (if only one player has cards left)
        for active_player in active_players:
            # Don't pass is_last_card_player flag here - we'll determine loser differently
            await check_player_status(room, active_player, is_last_card_player=False)
        
        # Check if game ended after checking player status
        if room.phase == GamePhase.WAITING:
            # Game is over, don't continue
            return
        
        # CRITICAL: Determine who was the LAST player to get rid of all cards
        # After pile discard, check all players who now have 0 cards
        # The player who just played (beat the card) was the LAST to get rid of cards
        players_with_no_cards = [p for p in room.players if not p.is_out and len(p.hand) == 0 and len(p.hidden_cards) == 0 and p.has_picked_hidden_cards]
        
        # If multiple players have no cards, the one who just played (player variable) was LAST
        if len(players_with_no_cards) > 1:
            # The 'player' variable is the one who just beat the card (played last)
            # This player was the LAST to get rid of all cards, so they LOSE
            # Reset all previous loser flags before marking new loser
            for p in room.players:
                p.is_loser = False
            
            player.is_out = True
            player.is_loser = True
            player.loss_count += 1
            logger.info(f"{player.username} was last to get rid of cards and loses!")
            
            await manager.send_personal_message(
                json.dumps({"type": "notification", "message": "You lost! You were the last to get rid of all cards. You'll get +1 hidden card next round. 😔"}),
                player.id
            )
            
            # Game finished
            room.phase = GamePhase.WAITING
            
            # Mark all other players with no cards as winners
            for p in players_with_no_cards:
                if p.id != player.id:
                    await manager.send_personal_message(
                        json.dumps({"type": "notification", "message": "You won! 🎉"}),
                        p.id
                    )
            
            # Track loser for first player determination in next game
            if not hasattr(room, 'losers_from_previous_game'):
                room.losers_from_previous_game = []
            room.losers_from_previous_game.append(player.id)
            
            # Reset all players to not ready for next round
            for p in room.players:
                p.ready = False
            
            # Send updated game state with WAITING phase
            await send_game_state(room)
            
            # Notify all players game ended
            await manager.broadcast_to_room(
                room.id,
                json.dumps({"type": "notification", "message": f"Game ended! {player.username} lost (last to get rid of cards) and will get +1 hidden card next round. Click Ready to play again!"})
            )
            return
        
        # If only one player has no cards, they might have won (check later after other logic)
        # If one player still has cards while others don't, that player loses
        
        # Check if only 1 player remains with cards - they are the loser
        remaining_players_with_cards = [p for p in room.players if not p.is_out and len(p.hand) > 0]
        all_remaining_players = [p for p in room.players if not p.is_out]
        
        if len(remaining_players_with_cards) == 1 and len(all_remaining_players) > 1:
            # One player has cards left, everyone else has no cards - that player loses
            # Reset all previous loser flags before marking new loser
            for p in room.players:
                p.is_loser = False
            
            loser = remaining_players_with_cards[0]
            loser.is_out = True
            loser.is_loser = True
            loser.loss_count += 1
            logger.info(f"{loser.username} is the last player with cards and loses!")
            
            await manager.send_personal_message(
                json.dumps({"type": "notification", "message": "You lost! You'll get +1 hidden card next round. 😔"}),
                loser.id
            )
            
            # Game finished
            room.phase = GamePhase.WAITING
            
            # Track loser for first player determination in next game
            if not hasattr(room, 'losers_from_previous_game'):
                room.losers_from_previous_game = []
            room.losers_from_previous_game.append(loser.id)
            
            # Reset all players to not ready for next round
            for p in room.players:
                p.ready = False
            
            # Send updated game state with WAITING phase
            await send_game_state(room)
            
            # Notify all players game ended
            await manager.broadcast_to_room(
                room.id,
                json.dumps({"type": "notification", "message": f"Game ended! {loser.username} lost and will get +1 hidden card next round. Click Ready to play again!"})
            )
            return
        elif len(all_remaining_players) == 1:
            # Only one player left total (everyone else is out) - they are the loser
            # Reset all previous loser flags before marking new loser
            for p in room.players:
                p.is_loser = False
            
            loser = all_remaining_players[0]
            loser.is_out = True
            loser.is_loser = True
            loser.loss_count += 1
            logger.info(f"{loser.username} is the last player remaining and loses!")
            
            await manager.send_personal_message(
                json.dumps({"type": "notification", "message": "You lost! You'll get +1 hidden card next round. 😔"}),
                loser.id
            )
            
            # Game finished
            room.phase = GamePhase.WAITING
            
            # Track loser for first player determination in next game
            if not hasattr(room, 'losers_from_previous_game'):
                room.losers_from_previous_game = []
            room.losers_from_previous_game.append(loser.id)
            
            # Reset all players to not ready for next round
            for p in room.players:
                p.ready = False
            
            # Send updated game state with WAITING phase
            await send_game_state(room)
            
            # Notify all players game ended
            await manager.broadcast_to_room(
                room.id,
                json.dumps({"type": "notification", "message": f"Game ended! {loser.username} lost and will get +1 hidden card next round. Click Ready to play again!"})
            )
            return
        
        # Check if current player can continue (has cards and not out)
        current_player = room.players[room.current_player_index]
        if current_player.is_out or len(current_player.hand) == 0:
            # Current player cannot continue, advance to next player
            room.current_player_index = (room.current_player_index + 1) % len(room.players)
            # Skip players who are out (with safety check to prevent infinite loop)
            checked_count = 0
            while room.players[room.current_player_index].is_out and checked_count < len(room.players):
                room.current_player_index = (room.current_player_index + 1) % len(room.players)
                checked_count += 1
            
            # If all players are out, game should have ended - this is a safety check
            if checked_count >= len(room.players):
                logger.warning("All players are OUT after pile discard - game should have ended!")
                # Force check if game ended
                active_players_after = [p for p in room.players if not p.is_out]
                if len(active_players_after) == 0:
                    # This shouldn't happen, but handle gracefully
                    room.phase = GamePhase.WAITING
                    return
        
        # Continue with current or next player after discard
        await send_game_state(room)
        return
    
    # Pile not full yet, move to next player
    await check_player_status(room, player)
    
    # Check if game ended (phase changed to WAITING)
    if room.phase == GamePhase.WAITING:
        # Game is over, send final state and don't advance turn
        await send_game_state(room)
        return
    
    room.current_player_index = (room.current_player_index + 1) % len(room.players)
    # Skip players who are out
    while room.players[room.current_player_index].is_out:
        room.current_player_index = (room.current_player_index + 1) % len(room.players)
    
    await send_game_state(room)

async def handle_take_pile(room: GameRoom, player: Player):
    """Handle taking the battle pile"""
    logger.info(f"handle_take_pile called by {player.username}")
    
    if room.players[room.current_player_index].id != player.id:
        logger.warning(f"{player.username} tried to take pile but not their turn")
        await manager.send_personal_message(
            json.dumps({"type": "error", "message": "Not your turn"}),
            player.id
        )
        return
    
    if player.is_out:
        logger.info(f"{player.username} is out, cannot take pile")
        return
    
    if not room.battle_pile:
        logger.info(f"Battle pile is empty, nothing to take")
        return
    
    # For 2 players: take all cards
    active_players = [p for p in room.players if not p.is_out]
    if len(active_players) == 2:
        # Take all cards from pile
        player.hand.extend(room.battle_pile)
        logger.info(f"{player.username} took {len(room.battle_pile)} cards from battle pile")
        room.battle_pile = []
        # Sort hand after taking cards (with trump suit priority)
        player.hand = sort_hand(player.hand, trump_suit=room.trump_suit)
        
        # Check all players for hidden card pickup (since pile is now empty)
        for p in room.players:
            await check_player_status(room, p)
        
        # Check if game ended after checking player status
        if room.phase == GamePhase.WAITING:
            # Game is over
            return
        
        # Move to next player
        room.current_player_index = (room.current_player_index + 1) % len(room.players)
        # Skip players who are out (with safety check)
        checked_count = 0
        while room.players[room.current_player_index].is_out and checked_count < len(room.players):
            room.current_player_index = (room.current_player_index + 1) % len(room.players)
            checked_count += 1
        
        await send_game_state(room)
        return
    
    # For 3+ players: take bottom card, leave rest
    if len(room.battle_pile) > 0:
        bottom_card = room.battle_pile.pop(0)
        player.hand.append(bottom_card)
        logger.info(f"{player.username} took bottom card: {bottom_card.rank} of {bottom_card.suit}")
        # Sort hand after taking card (with trump suit priority)
        player.hand = sort_hand(player.hand, trump_suit=room.trump_suit)
        
        # Check all players for hidden card pickup (bottom card removed from pile)
        for p in room.players:
            await check_player_status(room, p)
        
        # Check if game ended after checking player status
        if room.phase == GamePhase.WAITING:
            # Game is over
            return
        
        # Move to next player
        room.current_player_index = (room.current_player_index + 1) % len(room.players)
        # Skip players who are out (with safety check)
        checked_count = 0
        while room.players[room.current_player_index].is_out and checked_count < len(room.players):
            room.current_player_index = (room.current_player_index + 1) % len(room.players)
            checked_count += 1
        
        await send_game_state(room)

async def check_player_status(room: GameRoom, player: Player, is_last_card_player: bool = False):
    """Check if player should pick hidden cards, has won, or has lost
    
    Args:
        room: The game room
        player: The player to check
        is_last_card_player: True if this player played the LAST card that completed/discarded the pile
                           (this player LOSES if they have no cards left)
    """
    # Skip if player already out
    if player.is_out:
        return
    
    # Only check after player has played at least one card
    if not player.last_played_card:
        return
    
    # Check if player has no cards in hand
    if len(player.hand) == 0:
        # Player can only pick up hidden cards if battle pile was discarded (new pile started)
        # Battle pile must be empty for a new pile to have started
        new_pile_started = len(room.battle_pile) == 0
        
        # CRITICAL ENDGAME LOGIC:
        # When pile gets discarded and player has no cards:
        # - If is_last_card_player is True: apply old "last card loses" logic
        # - Otherwise, just mark as winner if appropriate
        if new_pile_started:
            # First check if player has won (no cards in hand, no hidden cards, pile is empty)
            if len(player.hidden_cards) == 0 and player.has_picked_hidden_cards:
                
                # Check if this player is the one who played the LAST card AND we're using that logic
                if is_last_card_player:
                    # OLD LOGIC: Player who played last card LOSES (only if multiple players still playing)
                    # This is now only used in specific scenarios, not after pile discard
                    active_players = [p for p in room.players if not p.is_out]
                    
                    # Only end game if there are multiple active players (2+)
                    # If only 1 player left and they played last card, they just lose normally
                    if len(active_players) >= 2:
                        # Reset all previous loser flags before marking new loser
                        for p in room.players:
                            p.is_loser = False
                        
                        player.is_out = True
                        player.is_loser = True
                        player.loss_count += 1  # Increment cumulative loss counter
                        logger.info(f"{player.username} played the last card and lost!")
                        
                        await manager.send_personal_message(
                            json.dumps({"type": "notification", "message": "You lost! You'll get +1 hidden card next round. 😔"}),
                            player.id
                        )
                        
                        # Game finished - all other active players won
                        room.phase = GamePhase.WAITING
                        
                        # Mark all other active players as winners
                        for p in room.players:
                            if not p.is_out and p.id != player.id:
                                p.is_out = True
                                logger.info(f"{p.username} has won!")
                                await manager.send_personal_message(
                                    json.dumps({"type": "notification", "message": "You won! 🎉"}),
                                    p.id
                                )
                        
                        # Track loser for first player determination in next game
                        if not hasattr(room, 'losers_from_previous_game'):
                            room.losers_from_previous_game = []
                        room.losers_from_previous_game.append(player.id)
                        
                        # Reset all players to not ready for next round
                        for p in room.players:
                            p.ready = False
                        
                        # Send updated game state with WAITING phase
                        await send_game_state(room)
                        
                        # Notify all players game ended
                        await manager.broadcast_to_room(
                            room.id,
                            json.dumps({"type": "notification", "message": f"Game ended! {player.username} lost and will get +1 hidden card next round. Click Ready to play again!"})
                        )
                else:
                    # Player has no cards, no hidden cards, and pile is empty (new pile started)
                    # This means they've gotten rid of all cards and should WIN
                    player.is_out = True
                    logger.info(f"{player.username} has won! (no cards, no hidden cards, pile empty)")
                    
                    await manager.send_personal_message(
                        json.dumps({"type": "notification", "message": "You won! 🎉"}),
                        player.id
                    )
                    
                    # Check if only one player remains (that player loses)
                    remaining_players = [p for p in room.players if not p.is_out]
                    if len(remaining_players) == 1:
                        # Last player remaining loses
                        loser = remaining_players[0]
                        
                        # Reset all previous loser flags
                        for p in room.players:
                            p.is_loser = False
                        
                        loser.is_out = True
                        loser.is_loser = True
                        loser.loss_count += 1
                        logger.info(f"{loser.username} is the last player remaining and loses!")
                        
                        await manager.send_personal_message(
                            json.dumps({"type": "notification", "message": "You lost! You'll get +1 hidden card next round. 😔"}),
                            loser.id
                        )
                        
                        # Game finished
                        room.phase = GamePhase.WAITING
                        
                        # Track loser
                        if not hasattr(room, 'losers_from_previous_game'):
                            room.losers_from_previous_game = []
                        room.losers_from_previous_game.append(loser.id)
                        
                        # Reset all players to not ready
                        for p in room.players:
                            p.ready = False
                        
                        # Send updated game state to all players
                        await send_game_state(room)
                        
                        # Notify all players
                        await manager.broadcast_to_room(
                            room.id,
                            json.dumps({"type": "notification", "message": f"Game ended! {loser.username} lost and will get +1 hidden card next round. Click Ready to play again!"})
                        )
            elif len(player.hidden_cards) > 0 and not player.has_picked_hidden_cards:
                # Pick up hidden cards (stashed cards from phase 1) only when new pile started
                player.hand.extend(player.hidden_cards)
                player.hidden_cards = []
                player.has_picked_hidden_cards = True
                # Sort hand after picking up hidden cards (with trump suit priority)
                player.hand = sort_hand(player.hand, trump_suit=room.trump_suit)
                logger.info(f"{player.username} picked up {len(player.hand)} hidden cards")
                
                await manager.send_personal_message(
                    json.dumps({"type": "notification", "message": "You picked up your stashed cards!"}),
                    player.id
                )

async def handle_beat_card(room: GameRoom, player: Player, message: dict):
    """Alias for handle_play_card for backward compatibility"""
    await handle_play_card(room, player, message)

async def transition_to_donation_phase(room: GameRoom):
    """Transition from phase 1 to donation phase"""
    # Check if any player has penalties (appears in bad_card_players list)
    if not room.bad_card_players:
        # No one needs donations, skip directly to Phase 2
        logger.info("No players need donations, skipping donation phase")
        await transition_to_phase_two(room)
        return
    
    # Aggregate consecutive entries for the same player
    # Example: [A, A, B, A] -> [A(2 cards), B(1 card), A(1 card)]
    aggregated_entries = []
    i = 0
    while i < len(room.bad_card_players):
        current_entry = room.bad_card_players[i]
        current_player_id = current_entry["player_id"]
        
        # Collect all consecutive entries for the same player
        reasons = [current_entry["reason"]]
        count = 1
        
        while i + count < len(room.bad_card_players) and room.bad_card_players[i + count]["player_id"] == current_player_id:
            reasons.append(room.bad_card_players[i + count]["reason"])
            count += 1
        
        # Create aggregated entry
        aggregated_entries.append({
            "player_id": current_player_id,
            "card_count": count,
            "reasons": reasons  # List of reasons
        })
        
        i += count
    
    # Replace bad_card_players with aggregated entries
    room.bad_card_players_aggregated = aggregated_entries
    
    room.phase = GamePhase.DONATION
    logger.info(f"Starting donation phase. Bad card players (aggregated): {[(room.get_player_by_id(entry['player_id']).username, entry['card_count']) for entry in aggregated_entries if room.get_player_by_id(entry['player_id'])]}")
    
    # Move all visible_stack cards to hand for all players (so they can donate everything)
    for player in room.players:
        if player.visible_stack:
            player.hand.extend(player.visible_stack)
            player.visible_stack = []
        logger.info(f"{player.username}: {len(player.hand)} cards in hand")
    
    # Initialize donation tracking
    # Track who has donated for EACH aggregated entry
    # donation_tracker[index_in_list] = {donor_id: donated_count} - how many cards they've donated
    room.donation_tracker = {}
    for idx in range(len(aggregated_entries)):
        room.donation_tracker[idx] = {}
        recipient_id = aggregated_entries[idx]["player_id"]
        cards_needed = aggregated_entries[idx]["card_count"]
        for player in room.players:
            if player.id != recipient_id:
                room.donation_tracker[idx][player.id] = 0  # Haven't donated yet (need to donate cards_needed)
    
    # Track current position in aggregated list
    room.current_donation_index = 0  # Which entry in bad_card_players we're currently processing
    
    # Reset player donation flags
    for player in room.players:
        player.pending_donations = {}
        player.has_donated = False
    
    await send_game_state(room)

def player_needs_to_donate(room: GameRoom, player: Player) -> bool:
    """Check if a player needs to donate cards based on bad_card_players list"""
    # Player needs to donate if they have cards AND there are recipients who haven't received from them yet
    if len(player.hand) == 0:
        return False
    
    # Check each entry in bad_card_players list
    for entry in room.bad_card_players:
        recipient_id = entry["player_id"]
        if recipient_id == player.id:
            continue  # Skip if this entry is the player themselves
        
        # Check if this donor has already donated for this specific entry
        if recipient_id in room.donation_tracker:
            if player.id in room.donation_tracker[recipient_id]:
                if room.donation_tracker[recipient_id][player.id] == 0:
                    return True  # Haven't donated for this entry yet
    
    return False

async def advance_donation_turn(room: GameRoom):
    """Advance to next player in donation phase or transition to phase 2 if complete"""
    # Check if donation phase is complete
    # Complete when all entries in bad_card_players have received 1 card from each other player
    donations_complete = True
    for recipient_id in room.bad_card_players:
        if recipient_id in room.donation_tracker:
            for donor_id, donated_count in room.donation_tracker[recipient_id].items():
                # Each donor should have donated exactly 1 card for each entry
                if donated_count == 0:
                    # Check if donor has cards
                    donor = room.get_player_by_id(donor_id)
                    if donor and len(donor.hand) > 0:
                        donations_complete = False
                        break
        if not donations_complete:
            break
    
    if donations_complete:
        logger.info("All donations completed, transitioning to phase 2")
        # Clear the bad_card_players list after donations complete
        room.bad_card_players = []
        await transition_to_phase_two(room)
    else:
        # Move to next player who needs to donate
        attempts = 0
        max_attempts = len(room.players)
        
        while attempts < max_attempts:
            room.current_player_index = (room.current_player_index + 1) % len(room.players)
            current_player = room.players[room.current_player_index]
            
            # Skip if player has no one to donate to (auto-mark as done)
            if not player_needs_to_donate(room, current_player):
                logger.info(f"{current_player.username} has no one to donate to, auto-skipping")
                current_player.has_donated = True
                attempts += 1
                continue
            
            # Skip if player has no cards to donate (auto-mark as done)
            if len(current_player.hand) == 0:
                logger.info(f"{current_player.username} has no cards to donate, auto-skipping")
                current_player.has_donated = True
                attempts += 1
                continue
            
            # Found a player who needs to donate
            logger.info(f"Moving to next donator: {current_player.username} (index {room.current_player_index})")
            await send_game_state(room)
            return
        
        # All players processed, check if we should transition
        logger.info("All players checked, transitioning to phase 2")
        await transition_to_phase_two(room)

async def handle_donate_cards(room: GameRoom, player: Player, message: dict):
    """Handle card donation - player donates to current recipient"""
    if room.phase != GamePhase.DONATION:
        logger.info(f"Donation attempted in wrong phase: {room.phase}")
        return
    
    # Check if current donation index is valid
    if room.current_donation_index >= len(room.bad_card_players_aggregated):
        logger.warning("Donation attempted but no more recipients")
        return
    
    current_recipient_entry = room.bad_card_players_aggregated[room.current_donation_index]
    current_recipient_id = current_recipient_entry["player_id"]
    cards_needed = current_recipient_entry["card_count"]
    current_recipient = room.get_player_by_id(current_recipient_id)
    
    if not current_recipient:
        logger.error(f"Current recipient {current_recipient_id} not found")
        return
    
    # Player can only donate if they're not the recipient
    if player.id == current_recipient_id:
        await manager.send_personal_message(
            json.dumps({"type": "error", "message": "You cannot donate to yourself"}), 
            player.id
        )
        return
    
    # Check how many cards this player has already donated for this entry
    already_donated = room.donation_tracker[room.current_donation_index].get(player.id, 0)
    
    if already_donated >= cards_needed:
        await manager.send_personal_message(
            json.dumps({"type": "error", "message": f"You already donated {cards_needed} card(s) for this recipient"}), 
            player.id
        )
        return
    
    donations = message.get("donations", {})  # {target_player_id: [card_indices]}
    logger.info(f"Player {player.username} donating: {donations}")
    
    # Validate that donation is to the current recipient
    if current_recipient_id not in donations:
        await manager.send_personal_message(
            json.dumps({"type": "error", "message": f"You must donate to {current_recipient.username}"}), 
            player.id
        )
        return
    
    card_indices = donations[current_recipient_id]
    
    # Donate the required number of cards (up to cards_needed)
    cards_to_donate = min(len(card_indices), cards_needed - already_donated)
    
    if cards_to_donate > 0 and len(player.hand) >= cards_to_donate:
        # Sort indices in descending order to remove from end first (avoid index issues)
        sorted_indices = sorted(card_indices[:cards_to_donate], reverse=True)
        
        for idx in sorted_indices:
            if 0 <= idx < len(player.hand):
                card = player.hand.pop(idx)
                current_recipient.hand.append(card)
                logger.info(f"Donated {card.rank} of {card.suit} from {player.username} to {current_recipient.username}")
        
        # Update donation tracker with new count
        room.donation_tracker[room.current_donation_index][player.id] = already_donated + cards_to_donate
        logger.info(f"Updated donation tracker for {player.username}: {already_donated} + {cards_to_donate} = {already_donated + cards_to_donate} (needed: {cards_needed})")
        
        # Check if all donors have donated the required cards for this entry
        # Each donor should donate cards_needed cards to the recipient
        all_donated = True
        for donor_id, donated_count in room.donation_tracker[room.current_donation_index].items():
            donor = room.get_player_by_id(donor_id)
            # Skip donors who have no cards left
            if donor and len(donor.hand) > 0:
                # Each donor must donate cards_needed cards
                if donated_count < cards_needed:
                    logger.info(f"Donor {donor.username} has donated {donated_count}/{cards_needed} cards, still waiting")
                    all_donated = False
                    break
        
        logger.info(f"All donated check: {all_donated}")
        
        if all_donated:
            # Move to next recipient in the list
            room.current_donation_index += 1
            logger.info(f"All players donated {cards_needed} card(s) for entry {room.current_donation_index}. Moving to next recipient.")
            
            # Check if we've processed all entries
            if room.current_donation_index >= len(room.bad_card_players_aggregated):
                # Donation phase complete
                logger.info("All donations complete, transitioning to phase 2")
                room.bad_card_players = []  # Clear the original list
                room.bad_card_players_aggregated = []  # Clear aggregated list
                await transition_to_phase_two(room)
                return
        
        # Send updated state
        await send_game_state(room)
    else:
        await manager.send_personal_message(
            json.dumps({"type": "error", "message": "No valid card to donate"}), 
            player.id
        )
        await send_game_state(room)

def sort_hand(hand: List[Card], trump_suit: Optional[str] = None) -> List[Card]:
    """Sort hand by suit and then by rank (descending) within each suit.
    
    For phase 2 (when trump_suit is provided):
    - 7 of spades always comes first (most powerful card)
    - Trump suit comes first (after 7 of spades if present)
    - Other spades come next (powerful beating cards)
    - Remaining suits follow
    - Within each suit, cards are sorted by rank descending (high to low)
    """
    def sort_key(card: Card):
        # 7 of spades is always first
        if card.suit == 'spades' and card.rank == 7:
            return (0, 0)  # Highest priority
        
        # If trump suit is specified (phase 2)
        if trump_suit:
            if card.suit == trump_suit:
                return (1, -card.rank)  # Trump suit first (after 7 of spades)
            elif card.suit == 'spades':
                return (2, -card.rank)  # Other spades second
            else:
                # Non-trump, non-spade suits
                suit_order = {'hearts': 3, 'diamonds': 4, 'clubs': 5}
                return (suit_order.get(card.suit, 6), -card.rank)
        else:
            # Phase 1: regular suit order
            suit_order = {'hearts': 0, 'diamonds': 1, 'clubs': 2, 'spades': 3}
            return (suit_order.get(card.suit, 4), -card.rank)
    
    return sorted(hand, key=sort_key)

async def transition_to_phase_two(room: GameRoom):
    """Transition to phase 2 (battle phase)"""
    logger.info("Transitioning to Phase 2 (Battle)")
    room.phase = GamePhase.PHASE_TWO
    
    # Reveal trump suit (it was determined when last card was drawn in phase 1)
    if not room.trump_suit and room.drawn_cards_order:
        room.determine_trump_suit()
        logger.info(f"Trump suit determined: {room.trump_suit}")
    
    # Move all visible_stack cards to player hands (only for players who still have cards in stacks)
    for player in room.players:
        if player.visible_stack:
            player.hand.extend(player.visible_stack)
            player.visible_stack = []
        logger.info(f"Player {player.username} now has {len(player.hand)} cards in hand")
    
    # Check if any players have empty hands and need to pick up hidden cards immediately
    for player in room.players:
        if not player.hand and player.hidden_cards and not player.has_picked_hidden_cards:
            # Player donated all cards, pick up hidden cards now
            player.hand.extend(player.hidden_cards)
            player.hidden_cards = []
            player.has_picked_hidden_cards = True
            logger.info(f"{player.username} donated all cards, picked up {len(player.hand)} hidden cards at start of Phase 2")
    
    # Sort all players' hands by suit and rank (with trump suit priority for phase 2)
    for player in room.players:
        if player.hand:
            player.hand = sort_hand(player.hand, trump_suit=room.trump_suit)
            logger.info(f"Sorted hand for {player.username}")
    
    # bad_card_counter removed - using room.bad_card_players list (already cleared in advance_donation_turn)
    
    # Determine first player (who drew last card from deck in phase 1)
    if room.last_deck_card_player:
        last_player_index = next((i for i, p in enumerate(room.players) if p.id == room.last_deck_card_player.id), 0)
        room.current_player_index = last_player_index
        logger.info(f"First player in Phase 2: {room.players[room.current_player_index].username} (drew last deck card)")
    else:
        # Fallback to first player if no last_deck_card_player is set
        room.current_player_index = 0
        logger.info(f"No last deck card player found, defaulting to first player")
    
    # Reset battle pile
    room.battle_pile = []
    
    await send_game_state(room)

async def send_game_state(room: GameRoom):
    for player in room.players:
        # Show last drawn card to ALL players in phase_one
        show_last_drawn = bool(room.last_drawn_card)
        
        game_state = {
            "type": "game_state",
            "phase": room.phase.value,
            "players": [],
            "current_player_index": room.current_player_index,
            "trump_suit": room.trump_suit,
            "deck_size": len(room.deck),
            "battle_pile": [card.to_dict() for card in room.battle_pile],
            "discarded_count": len(getattr(room, 'discarded_cards', [])),
            "last_drawn_card": room.last_drawn_card.to_dict() if show_last_drawn else None,
            "player_id": player.id,
            "bad_card_players": room.bad_card_players,  # Original list with all penalties
            "bad_card_players_aggregated": getattr(room, 'bad_card_players_aggregated', []),  # Aggregated for donation
            "current_donation_index": getattr(room, 'current_donation_index', 0),  # Which entry is being processed
            "donation_tracker": getattr(room, 'donation_tracker', {}),  # Include donation progress
            "pile_discard_in_progress": room.pile_discard_in_progress  # Flag for 3-second delay
        }
        
        # Add player info
        for p in room.players:
            player_info = {
                "id": p.id,
                "username": p.username,
                "ready": p.ready,
                "hand_size": len(p.hand),
                "visible_stack": [card.to_dict() for card in p.visible_stack],
                # bad_card_counter removed - using room.bad_card_players list instead
                "is_out": p.is_out,
                "has_donated": getattr(p, 'has_donated', False),
                "has_picked_hidden_cards": p.has_picked_hidden_cards,
                "hidden_cards_count": len(p.hidden_cards),  # Show count to everyone
                "is_loser": p.is_loser  # Show loser status for emoji display
            }
            
            # Only show own hand and locked cards
            if p.id == player.id:
                player_info["hand"] = [card.to_dict() for card in p.hand]
                player_info["hidden_cards"] = [card.to_dict() for card in p.hidden_cards]
                # Send locked stack cards info
                if hasattr(p, 'locked_stack_cards'):
                    player_info["locked_stack_cards"] = [{"suit": suit, "rank": rank} for suit, rank in p.locked_stack_cards]
                else:
                    player_info["locked_stack_cards"] = []
            
            game_state["players"].append(player_info)
        
        await manager.send_personal_message(json.dumps(game_state), player.id)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
