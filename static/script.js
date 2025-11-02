let ws = null;
let gameState = {};
let currentUsername = '';
let currentRoomId = '';
let draggedCard = null;

// Initialize
document.addEventListener('DOMContentLoaded', function() {
    refreshRooms();
    
    // Enable drag and drop
    document.addEventListener('dragstart', handleDragStart);
    document.addEventListener('dragover', handleDragOver);
    document.addEventListener('drop', handleDrop);
    document.addEventListener('dragend', handleDragEnd);
});

// Room management functions
async function refreshRooms() {
    try {
        const response = await fetch('/api/rooms');
        const data = await response.json();
        
        const container = document.getElementById('roomsContainer');
        if (data.rooms.length === 0) {
            container.innerHTML = '<p>No rooms available</p>';
        } else {
            container.innerHTML = data.rooms.map(room => 
                `<div class="room-item" onclick="joinRoom('${room.id}')">
                    <span>Room ${room.id}</span>
                    <span>${room.players}/${room.max_players} players</span>
                </div>`
            ).join('');
        }
    } catch (error) {
        console.error('Error loading rooms:', error);
        showNotification('Error loading rooms', 'error');
    }
}

async function createRoom() {
    const username = document.getElementById('usernameInput').value.trim();
    if (!username) {
        showNotification('Please enter a username', 'error');
        return;
    }

    try {
        const response = await fetch('/api/rooms', { 
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            }
        });
        const data = await response.json();
        joinRoom(data.room_id);
    } catch (error) {
        console.error('Error creating room:', error);
        showNotification('Error creating room', 'error');
    }
}

function joinRoom(roomId) {
    const username = document.getElementById('usernameInput').value.trim();
    if (!username) {
        showNotification('Please enter a username', 'error');
        return;
    }

    currentUsername = username;
    currentRoomId = roomId;
    
    // Connect to WebSocket
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws/${roomId}/${encodeURIComponent(username)}`;
    
    ws = new WebSocket(wsUrl);
    
    ws.onopen = function() {
        document.getElementById('welcomeScreen').style.display = 'none';
        document.getElementById('gameScreen').style.display = 'block';
        document.getElementById('roomId').textContent = `Room: ${roomId}`;
        showNotification('Connected to room!', 'success');
    };
    
    ws.onmessage = function(event) {
        const data = JSON.parse(event.data);
        handleGameStateUpdate(data);
    };
    
    ws.onclose = function() {
        showNotification('Connection closed', 'error');
        // Return to welcome screen
        document.getElementById('welcomeScreen').style.display = 'block';
        document.getElementById('gameScreen').style.display = 'none';
    };
    
    ws.onerror = function(error) {
        console.error('WebSocket error:', error);
        showNotification('Connection error', 'error');
    };
}

// Game state management
function handleGameStateUpdate(data) {
    if (data.type === 'game_state') {
        const previousPhase = gameState?.phase;
        gameState = data;
        
        // Check if game just ended (transition from phase_two to waiting)
        if (previousPhase === 'phase_two' && gameState.phase === 'waiting') {
            // Game just ended, will show special modal when notification arrives
            console.log('Game ended - transitioning from phase_two to waiting');
        }
        
        updateUI();
    } else if (data.type === 'error') {
        showNotification(data.message, 'error');
    } else if (data.type === 'notification') {
        // Check if this is a game-end notification
        const message = data.message;
        console.log('Received notification:', message);
        
        if (message.includes('Game ended!') && message.includes('lost')) {
            // Extract loser name from message like "Game ended! PlayerName lost and will get +1 hidden card next round..."
            const match = message.match(/Game ended! (.+?) lost/);
            console.log('Game end notification detected, regex match:', match);
            
            if (match) {
                const loserName = match[1];
                console.log('Showing game end modal for loser:', loserName);
                showGameEndModal(loserName);
                // Don't show the regular notification for game end
                return;
            }
        }
        
        // Show regular notification for non-game-end messages
        showNotification(message, 'info');
    }
}

function updateUI() {
    // Handle phase 2 UI FIRST (before updateGameActions)
    if (gameState.phase === 'phase_two') {
        updatePhase2UI();
    } else {
        // Clean up Phase 2 UI when not in phase 2 - do this BEFORE updateGameActions
        cleanupPhase2UI();
    }
    
    updatePhaseDisplay();
    updatePlayersDisplay();
    updateGameBoard();
    updateHand();
    updateGameActions();
    
    // Handle donation phase
    if (gameState.phase === 'donation') {
        const isMyTurn = gameState.players && 
            gameState.players[gameState.current_player_index]?.id === gameState.player_id;
        if (isMyTurn) {
            showDonationUI();
            hideWaitingModal();
        } else {
            hideDonationUI();
            showWaitingModal('Donation Phase', 'Please wait while other players donate their bad cards...');
        }
    } else {
        hideDonationUI();
        hideWaitingModal();
    }
}

function cleanupPhase2UI() {
    // Remove Phase 2 UI elements
    const gameActions = document.getElementById('gameActions');
    if (gameActions) {
        const existingPhase2 = gameActions.querySelector('.phase2-actions');
        if (existingPhase2) {
            existingPhase2.remove();
        }
    }
    
    // Restore Phase 1 buttons visibility
    const drawButton = document.getElementById('drawButton');
    const endTurnButton = document.getElementById('endTurnButton');
    if (drawButton) {
        drawButton.style.display = '';  // Reset to default
    }
    if (endTurnButton) {
        endTurnButton.style.display = '';  // Reset to default
    }
    
    // Remove battle pile drop zone functionality
    const battlePile = document.getElementById('battlePile');
    if (battlePile) {
        battlePile.classList.remove('drop-zone-active');
        battlePile.removeAttribute('data-target');
        battlePile.removeEventListener('dragover', handleDragOver);
        battlePile.removeEventListener('drop', handleDrop);
    }
}

function updateGameActions() {
    const gameActions = document.getElementById('gameActions');
    const drawButton = document.getElementById('drawButton');
    const endTurnButton = document.getElementById('endTurnButton');
    
    if (gameState.phase !== 'phase_one') {
        gameActions.classList.add('hidden');
        return;
    }

    const isMyTurn = gameState.players && 
        gameState.players[gameState.current_player_index]?.id === gameState.player_id;
    
    if (!isMyTurn) {
        gameActions.classList.add('hidden');
        return;
    }

    gameActions.classList.remove('hidden');
    
    const myPlayer = gameState.players?.find(p => p.id === gameState.player_id);
    if (!myPlayer) return;

    const hasHandCard = myPlayer.hand && myPlayer.hand.length > 0;

    // Show draw button if player has no cards in hand and deck exists
    const canDraw = gameState.deck_size > 0 && !hasHandCard;
    drawButton.style.display = canDraw ? 'block' : 'none';

    // Change end turn to "Place on My Stack" when player has a card in hand
    if (hasHandCard) {
        endTurnButton.textContent = 'Положить собі';
        endTurnButton.onclick = placeCardOnOwnStack;
        endTurnButton.style.display = 'block';
    } else {
        endTurnButton.textContent = 'Закончить ход';
        endTurnButton.onclick = endTurn;
        // Only show end turn if no actions are required
        endTurnButton.style.display = !canDraw ? 'block' : 'none';
    }
}

function updatePhaseDisplay() {
    const phaseText = document.getElementById('phaseText');
    const gamePhase = document.getElementById('gamePhase');
    const trumpInfo = document.getElementById('trumpInfo');
    const trumpSuit = document.getElementById('trumpSuit');
    const trumpIndicator = document.getElementById('trumpIndicator');
    const readyButton = document.getElementById('readyButton');
    const currentPlayerElement = document.getElementById('currentPlayer');
    const deckArea = document.getElementById('deckArea');
    const discardedPile = document.getElementById('discardedPile');
    
    // Safety checks for required elements
    if (!phaseText || !gamePhase || !readyButton || !deckArea) {
        console.error('Missing required DOM elements for phase display');
        return;
    }
    
    switch(gameState.phase) {
        case 'waiting':
            phaseText.textContent = 'Ждем';
            gamePhase.textContent = 'Phase: Waiting';
            readyButton.style.display = 'block';
            
            // Update ready button state based on player's ready status
            const myPlayer = gameState.players?.find(p => p.id === gameState.player_id);
            if (myPlayer) {
                if (myPlayer.ready) {
                    readyButton.textContent = 'Ждем остальних...';
                    readyButton.disabled = true;
                } else {
                    readyButton.textContent = 'Готов';
                    readyButton.disabled = false;
                }
            }
            
            deckArea.style.display = 'flex';
            if (discardedPile) discardedPile.style.display = 'none';
            break;
        case 'phase_one':
            phaseText.textContent = 'Тянем карти з колоди';
            gamePhase.textContent = 'Тянем потянем';
            readyButton.style.display = 'none';
            deckArea.style.display = 'flex';
            if (discardedPile) discardedPile.style.display = 'none';
            break;
        case 'donation':
            phaseText.textContent = 'Даєм погані карти';
            gamePhase.textContent = 'По х*йовой раз два три!';
            readyButton.style.display = 'none';
            deckArea.style.display = 'none';
            if (discardedPile) discardedPile.style.display = 'none';
            showDonationUI();
            break;
        case 'phase_two':
            phaseText.textContent = 'Будeм бітса?';
            gamePhase.textContent = 'Замес іде';
            readyButton.style.display = 'none';
            deckArea.style.display = 'none';
            if (discardedPile) discardedPile.style.display = 'flex';
            break;
    }

    // Only show trump info in phase 2 or later
    if (gameState.trump_suit && gameState.phase !== 'waiting' && gameState.phase !== 'phase_one') {
        if (trumpInfo) trumpInfo.classList.remove('hidden');
        if (trumpSuit) trumpSuit.textContent = getSuitSymbol(gameState.trump_suit);
        if (trumpIndicator) {
            trumpIndicator.classList.remove('hidden');
            trumpIndicator.textContent = `Trump: ${getSuitSymbol(gameState.trump_suit)}`;
        }
    } else {
        if (trumpInfo) trumpInfo.classList.add('hidden');
        if (trumpIndicator) trumpIndicator.classList.add('hidden');
    }

    // Update current player
    if (gameState.players && gameState.current_player_index !== undefined && currentPlayerElement) {
        const currentPlayer = gameState.players[gameState.current_player_index];
        currentPlayerElement.textContent = 
            `Current: ${currentPlayer ? currentPlayer.username : 'None'}`;
    }

    // Update deck area visibility and state
    if (gameState.phase === 'phase_two') {
        deckArea.style.display = 'none';
    } else {
        if (gameState.deck_size > 0) {
            deckArea.classList.add('has-cards');
        } else {
            deckArea.classList.remove('has-cards');
        }
    }

    // Show game instructions based on phase
    updateGameInstructions();
}

function updateGameInstructions() {
    const myPlayer = gameState.players?.find(p => p.id === gameState.player_id);
    const isMyTurn = gameState.players && 
        gameState.players[gameState.current_player_index]?.id === gameState.player_id;
    
    let instruction = '';
    
    if (gameState.phase === 'waiting') {
        instruction = 'Click Ready when you\'re ready to start!';
    } else if (gameState.phase === 'phase_one') {
        if (isMyTurn) {
            const hasHandCard = myPlayer.hand && myPlayer.hand.length > 0;
            const hasStackCards = myPlayer.visible_stack && myPlayer.visible_stack.length > 1;
            
            if (!hasHandCard && !hasStackCards) {
                instruction = 'Витягти карту з колоди';
            } else if (!hasHandCard && hasStackCards) {
                instruction = 'Перетягніть карти зі своєї купи до інших, або витягніть з колоди';
            } else if (hasHandCard) {
                instruction = 'Ваш хід';
            }
        } else {
            const currentPlayer = gameState.players[gameState.current_player_index];
            instruction = `${currentPlayer ? currentPlayer.username : 'Player'}\'s хід`;
        }
    } else if (gameState.phase === 'finished') {
        // Find winners and losers
        const winners = gameState.players?.filter(p => p.is_out) || [];
        const losers = gameState.players?.filter(p => !p.is_out) || [];
        
        if (winners.length > 0 && losers.length > 0) {
            const winnerNames = winners.map(p => p.username).join(', ');
            const loserNames = losers.map(p => p.username).join(', ');
            instruction = `${winnerNames} виграли! ${loserNames} отримає додаткові карти в прикуп в наступному раунді.`;
        } else {
            instruction = 'Гра закінчена! Натисніть «Готов», щоб почати новий раунд.';
        }
    }
    
    // Only show instruction if it's different from the last one
    if (instruction && instruction !== window.lastInstruction) {
        window.lastInstruction = instruction;
        showNotification(instruction, 'info');
    }
}

function updatePlayersDisplay() {
    const container = document.getElementById('playersContainer');
    const playersArea = document.getElementById('playersArea');
    
    if (!gameState.players) return;

    // Update sidebar
    container.innerHTML = gameState.players.map(player => {
        const isCurrentTurn = gameState.players[gameState.current_player_index]?.id === player.id;
        const classes = `player-item ${player.ready ? 'ready' : ''} ${isCurrentTurn ? 'current-turn' : ''}`;
        const hasPickedHidden = player.has_picked_hidden_cards ? '🔥' : '';
        const hiddenCount = player.hidden_cards_count || 0;
        
        return `
            <div class="${classes}">
                <span>${player.username} ${hasPickedHidden} ${player.id === gameState.player_id ? '(You)' : ''}</span>
                <div>
                    <span>Stack: ${player.visible_stack ? player.visible_stack.length : 0}</span>
                    ${hiddenCount > 0 && !player.has_picked_hidden_cards ? `<br><small style="color: #d01632ff;">Прикуп: ${hiddenCount}</small>` : ''}
                    ${player.bad_card_counter > 0 ? `<br><small style="color: #e74c3c;">Погані: ${player.bad_card_counter}</small>` : ''}
                </div>
            </div>
        `;
    }).join('');

    // Update players area on game board
    playersArea.innerHTML = gameState.players.map(player => {
        const isCurrentPlayer = gameState.players[gameState.current_player_index]?.id === player.id;
        const isMe = player.id === gameState.player_id;
        const hasPickedHidden = player.has_picked_hidden_cards ? '🔥' : '';
        
        // In Phase 2, show hand cards and hidden cards
        if (gameState.phase === 'phase_two') {
            const handSize = player.hand ? player.hand.length : (player.hand_size || 0);
            const hiddenCount = player.hidden_cards_count || 0;
            
            // Generate array of hidden card placeholders
            const hiddenCardsArray = Array(hiddenCount).fill({});
            
            return `
                <div class="player-area ${isCurrentPlayer ? 'current-player' : ''}" data-player-id="${player.id}">
                    <div class="card-area">
                        ${hiddenCount > 0 && !hasPickedHidden ? `<div class="hidden-cards-area">${renderHiddenCards(hiddenCardsArray)}</div>` : ''}
                        ${isMe ? '' : renderPlayerHandHidden(handSize)}
                    </div>
                    <div class="player-info">
                        <h4>${player.username} ${hasPickedHidden} ${isMe ? '(You)' : ''} ${isCurrentPlayer ? '👈' : ''}</h4>
                        <p>Cards: ${handSize}${player.is_out ? ' | OUT' : ''}</p>
                    </div>
                </div>
            `;
        }
        
        // Phase 1 and other phases
        const hiddenCount = player.hidden_cards_count || 0;
        const hiddenCardsArray = Array(hiddenCount).fill({});
        
        return `
            <div class="player-area ${isCurrentPlayer ? 'current-player' : ''}" data-player-id="${player.id}">
                <div class="card-area">
                    ${hiddenCount > 0 && !hasPickedHidden ? `<div class="hidden-cards-area">${renderHiddenCards(hiddenCardsArray)}</div>` : ''}
                    <div class="visible-stack" id="stack-${player.id}">
                        ${renderVisibleStack(player.visible_stack, player.id)}
                    </div>
                </div>
                <div class="player-info">
                    <h4>${player.username} ${hasPickedHidden} ${isMe ? '(You)' : ''} ${isCurrentPlayer ? '👈' : ''}</h4>
                    <p>Стопка: ${player.visible_stack ? player.visible_stack.length : 0}${player.bad_card_counter > 0 ? ` | Bad: ${player.bad_card_counter}` : ''}</p>
                </div>
            </div>
        `;
    }).join('');

    // Make player stacks drop zones (only in Phase 1)
    if (gameState.phase === 'phase_one') {
        gameState.players.forEach(player => {
            const stackElement = document.getElementById(`stack-${player.id}`);
            if (stackElement) {
                stackElement.addEventListener('dragover', handleDragOver);
                stackElement.addEventListener('drop', handleDrop);
                stackElement.dataset.playerId = player.id;
            }
        });
    }
}

function renderPlayerHandHidden(cardCount) {
    if (!cardCount || cardCount === 0) {
        return '<p style="color: rgba(255,255,255,0.6); font-size: 0.9rem;">No cards</p>';
    }
    
    // Show minified card backs for other players' hands in Phase 2
    return `<div class="player-hand-hidden">
        ${Array.from({length: Math.min(cardCount, 10)}).map((_, i) => 
            `<div class="mini-card-back" style="left: ${i * 8}px; z-index: ${i};"></div>`
        ).join('')}
        ${cardCount > 10 ? `<span style="color: white; margin-left: ${10 * 8 + 40}px; position: relative; z-index: 11;">+${cardCount - 10}</span>` : ''}
    </div>`;
}

function renderVisibleStack(stack, playerId) {
    if (!stack || stack.length === 0) {
        return '<div class="drop-zone">Drop cards here</div>';
    }

    const isMyStack = playerId === gameState.player_id;
    const isMyTurn = gameState.players && 
        gameState.players[gameState.current_player_index]?.id === gameState.player_id;

    return stack.map((card, index) => {
        const isTopCard = index === stack.length - 1;
        const isSecondToTop = index === stack.length - 2;
        
        // Show all cards with different visibility levels
        let leftOffset, topOffset, cardClass;
        if (isTopCard) {
            // Top card: full visibility with offset to show second card
            leftOffset = 35;
            topOffset = 0;
            cardClass = 'stack-top-card';
        } else if (isSecondToTop) {
            // Second to top: partially visible underneath (show left edge)
            leftOffset = 0;
            topOffset = 0;
            cardClass = 'stack-second-card';
        } else {
            // Other cards: just a thin edge visible (stacked underneath)
            leftOffset = -5 * (stack.length - 1 - index); // Negative offset for tucked cards
            topOffset = 2 * (stack.length - 1 - index);
            cardClass = 'stack-hidden-card';
        }
        
        // Check if this card can be dragged from stack
        let canDragFromStack = false;
        if (isMyStack && isMyTurn && stack.length > 1 && isTopCard) {
            // Check if card is locked (player drew instead of giving it)
            const myPlayer = gameState.players?.find(p => p.id === gameState.player_id);
            const isLocked = myPlayer?.locked_stack_cards?.some(
                locked => locked.suit === card.suit && locked.rank === card.rank
            );
            
            // Allow dragging if not locked (player can attempt to give, even if it violates seniority)
            // Backend will handle penalties for invalid moves
            canDragFromStack = !isLocked;
        }
        
        console.log('Stack card render:', {
            playerId,
            cardRank: card.rank,
            cardSuit: card.suit,
            isMyStack,
            isMyTurn,
            stackLength: stack.length,
            isTopCard,
            isSecondToTop,
            canDragFromStack,
            cardClass
        });
        
        // Check if card is locked
        const myPlayer = gameState.players?.find(p => p.id === gameState.player_id);
        const isLocked = isMyStack && isTopCard && myPlayer?.locked_stack_cards?.some(
            locked => locked.suit === card.suit && locked.rank === card.rank
        );
        
        return `<div class="card ${card.suit} ${cardClass} ${canDragFromStack ? 'draggable-stack-card' : ''} ${isLocked ? 'locked-card' : ''}" 
                    style="z-index: ${index}; left: ${leftOffset}px; top: ${topOffset}px; position: absolute;"
                    ${canDragFromStack ? 'draggable="true"' : 'draggable="false"'}
                    data-card="${encodeURIComponent(JSON.stringify(card))}"
                    data-source="stack"
                    ${isMyStack && !isLocked ? `onclick="liftCard('${playerId}', ${index})"` : ''}>
            <div class="rank rank-top-left">${getRankSymbol(card.rank)}</div>
            <div class="suit suit-top-left">${getSuitSymbol(card.suit)}</div>
            <div class="rank rank-bottom-right">${getRankSymbol(card.rank)}</div>
            <div class="suit suit-bottom-right">${getSuitSymbol(card.suit)}</div>
        </div>`;
    }).join('');
}

function canStackCardOnTarget(card, targetStack) {
    if (!targetStack || targetStack.length === 0) return true;
    
    const topCard = targetStack[targetStack.length - 1];
    return card.rank === topCard.rank + 1 || (card.rank === 6 && topCard.rank === 14);
}

function liftCard(playerId, cardIndex) {
    // Prevent click action if we're dragging
    if (isDragging) {
        return;
    }
    
    const player = gameState.players?.find(p => p.id === playerId);
    if (!player || !player.visible_stack || cardIndex >= player.visible_stack.length) return;

    // Show the card underneath (if any)
    if (cardIndex > 0) {
        const underCard = player.visible_stack[cardIndex - 1];
        showNotification(`Card underneath: ${getRankSymbol(underCard.rank)}${getSuitSymbol(underCard.suit)}`, 'info');
    } else {
        showNotification('This is the bottom card', 'info');
    }
}

function renderHiddenCards(hiddenCards) {
    if (!hiddenCards || hiddenCards.length === 0) {
        return '';
    }

    return hiddenCards.map((card, index) => 
        `<div class="card hidden" style="z-index: ${index}; left: ${index * 5}px; top: ${index * 5}px;" title="Hidden card">
            <div class="rank">?</div>
            <div class="suit">?</div>
        </div>`
    ).join('');
}

function updateGameBoard() {
    // Update battle pile (only visible in phase_two)
    const battlePile = document.getElementById('battlePile');
    if (battlePile) {
        if (gameState.phase === 'phase_two') {
            battlePile.style.display = 'flex';
            if (gameState.battle_pile && gameState.battle_pile.length > 0) {
                battlePile.innerHTML = '<span class="pile-label">Battle Pile</span>' + gameState.battle_pile.map((card, index) => 
                    `<div class="card ${card.suit}" style="z-index: ${index + 1}; left: ${10 + index * 15}px; top: ${10 + index * 3}px; position: absolute;">
                        <div class="rank rank-top-left">${getRankSymbol(card.rank)}</div>
                        <div class="suit suit-top-left">${getSuitSymbol(card.suit)}</div>
                        <div class="rank rank-bottom-right">${getRankSymbol(card.rank)}</div>
                        <div class="suit suit-bottom-right">${getSuitSymbol(card.suit)}</div>
                    </div>`
                ).join('');
                battlePile.classList.remove('empty');
            } else {
                battlePile.innerHTML = '<span class="pile-label">Drop card here</span>';
                battlePile.classList.add('empty');
            }
        } else {
            // Hide battle pile in other phases
            battlePile.style.display = 'none';
        }
    }
    
    // Update discarded pile (show card backs for discarded cards in Phase 2)
    const discardedPile = document.getElementById('discardedPile');
    if (discardedPile) {
        if (gameState.phase === 'phase_two') {
            discardedPile.style.display = 'flex';
            const discardedCount = gameState.discarded_count || 0;
            if (discardedCount > 0) {
                const cardsToShow = Math.min(discardedCount, 8);
                let discardedHTML = '<span class="pile-label">Discarded</span>';
                for (let i = 0; i < cardsToShow; i++) {
                    const offset = (i / cardsToShow) * 6;
                    discardedHTML += `<div class="discarded-card-back" style="top: ${10 + offset}px; left: ${10 + offset}px; z-index: ${i};"></div>`;
                }
                discardedPile.innerHTML = discardedHTML;
            } else {
                discardedPile.innerHTML = '<span class="pile-label">Discarded</span>';
            }
        } else {
            // Hide discarded pile in other phases
            discardedPile.style.display = 'none';
        }
    }
    
    // Update deck rendering with stack effect only if deck area exists
    if (document.getElementById('deckArea') && gameState.phase !== 'phase_two') {
        updateDeckDisplay();
    }
    
    // Update last drawn card display (phase_one only)
    updateLastDrawnCard();
}

function updateDeckDisplay() {
    const deckArea = document.getElementById('deckArea');
    if (!deckArea) {
        console.error('Deck area element not found');
        return;
    }
    
    const deckSize = gameState.deck_size || 0;
    
    if (deckSize === 0) {
        deckArea.innerHTML = '<span class="deck-label">Empty Deck</span>';
        deckArea.classList.remove('has-cards');
        return;
    }
    
    deckArea.classList.add('has-cards');
    
    // Calculate how many cards to show in the visual stack (max 8 for performance)
    const cardsToShow = Math.min(deckSize, 8);
    const stackDepth = Math.min(cardsToShow * 0.8, 6); // Max 6px offset
    
    let deckHTML = '<span class="deck-label">Колода</span>';
    
    // Render stack of cards with offset
    for (let i = 0; i < cardsToShow; i++) {
        const offset = (i / cardsToShow) * stackDepth;
        deckHTML += `
            <div class="deck-card" style="
                position: absolute;
                top: ${10 + offset}px;
                left: ${10 + offset}px;
                z-index: ${i};
                width: 60px;
                height: 80px;
                background: #8B4513;
                border: 1px solid #654321;
                border-radius: 6px;
            "></div>`;
    }
    
    // Add trump indicator if it exists
    const trumpIndicator = document.getElementById('trumpIndicator');
    if (trumpIndicator) {
        deckHTML += trumpIndicator.outerHTML;
    }
    
    // Add count indicator (show in all phases when deck has cards)
    deckHTML += `<span class="deck-count" style="
        position: absolute;
        bottom: 5px;
        right: 5px;
        background: rgba(0,0,0,0.8);
        color: white;
        padding: 2px 6px;
        border-radius: 10px;
        font-size: 0.8rem;
        z-index: 100;
    ">${deckSize}</span>`;
    
    deckArea.innerHTML = deckHTML;
}

function updateLastDrawnCard() {
    // Show last drawn card for all players to see in phase_one
    if (gameState.phase !== 'phase_one' || !gameState.last_drawn_card) {
        // Hide or remove last drawn card display
        const existingDisplay = document.getElementById('lastDrawnCardDisplay');
        if (existingDisplay) {
            existingDisplay.remove();
        }
        return;
    }
    
    const card = gameState.last_drawn_card;
    const centerArea = document.querySelector('.center-area');
    
    if (!centerArea) return;
    
    // Check if display already exists
    let display = document.getElementById('lastDrawnCardDisplay');
    if (!display) {
        display = document.createElement('div');
        display.id = 'lastDrawnCardDisplay';
        display.style.cssText = `
            position: absolute;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            z-index: 150;
            text-align: center;
        `;
        centerArea.appendChild(display);
    }
    
    // Update display with current drawn card
    display.innerHTML = `
        <div style="
            background: rgba(0,0,0,0.9);
            padding: 15px;
            border-radius: 10px;
            border: 2px solid #f39c12;
        ">
            <div style="color: white; font-weight: bold; margin-bottom: 10px;">Last Drawn Card</div>
            <div class="card ${card.suit}" style="
                width: 80px;
                height: 110px;
                margin: 0 auto;
                position: relative;
            ">
                <div class="rank rank-top-left">${getRankSymbol(card.rank)}</div>
                <div class="suit suit-top-left">${getSuitSymbol(card.suit)}</div>
                <div class="rank rank-bottom-right">${getRankSymbol(card.rank)}</div>
                <div class="suit suit-bottom-right">${getSuitSymbol(card.suit)}</div>
            </div>
        </div>
    `;
}

function updateHand() {
    const hand = document.getElementById('playerHand');
    const myPlayer = gameState.players?.find(p => p.id === gameState.player_id);
    
    console.log('updateHand - myPlayer:', myPlayer);
    console.log('updateHand - myPlayer.hand:', myPlayer?.hand);
    
    if (!myPlayer || !myPlayer.hand) {
        hand.innerHTML = '';
        return;
    }

    hand.innerHTML = myPlayer.hand.map((card, index) => {
        console.log(`updateHand - rendering card ${index}:`, card);
        return `<div class="card ${card.suit}" draggable="true" data-card='${JSON.stringify(card)}' data-source="hand" data-index="${index}">
            <div class="rank rank-top-left">${getRankSymbol(card.rank)}</div>
            <div class="suit suit-top-left">${getSuitSymbol(card.suit)}</div>
            <div class="rank rank-bottom-right">${getRankSymbol(card.rank)}</div>
            <div class="suit suit-bottom-right">${getSuitSymbol(card.suit)}</div>
        </div>`;
    }).join('');
}

// Drag and drop handlers
let isDragging = false;

function handleDragStart(e) {
    console.log('handleDragStart triggered', e.target);
    
    if (!e.target.classList.contains('card')) {
        console.log('Not a card element');
        return;
    }
    
    if (!e.target.hasAttribute('data-card')) {
        console.log('Card has no data-card attribute');
        return;
    }
    
    if (e.target.getAttribute('draggable') === 'false') {
        console.log('Card is not draggable');
        return;
    }
    
    const cardData = e.target.getAttribute('data-card');
    const source = e.target.getAttribute('data-source');
    
    console.log('Drag data:', {
        cardData,
        source,
        element: e.target,
        classes: e.target.className
    });
    
    try {
        if (source === 'stack') {
            draggedCard = JSON.parse(decodeURIComponent(cardData));
        } else {
            draggedCard = JSON.parse(cardData);
        }
        draggedCard.source = source;
        console.log('Successfully parsed draggedCard:', draggedCard);
    } catch (error) {
        console.error('Error parsing card data:', error);
        return;
    }

    isDragging = true;
    e.target.classList.add('dragging');
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/html', e.target.outerHTML);
    
    console.log('Drag started successfully');
}function handleDragOver(e) {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    
    // Find the actual drop zone by traversing up from the target
    let dropZone = e.target;
    let foundDropZone = false;
    
    // Traverse up to find a valid drop zone (max 5 levels)
    for (let i = 0; i < 5 && dropZone; i++) {
        if (dropZone.classList && (
            dropZone.classList.contains('visible-stack') || 
            dropZone.classList.contains('battle-pile') ||
            dropZone.id === 'battlePile'
        )) {
            foundDropZone = true;
            break;
        }
        dropZone = dropZone.parentElement;
    }
    
    if (!foundDropZone || !dropZone) {
        return;
    }
    
    dropZone.classList.add('drag-over');
    
    if (canDropCard(draggedCard, dropZone)) {
        dropZone.classList.add('valid-drop');
        dropZone.classList.remove('invalid-drop');
    } else {
        dropZone.classList.add('invalid-drop');
        dropZone.classList.remove('valid-drop');
    }
}

function handleDrop(e) {
    e.preventDefault();
    
    // Find the actual drop zone by traversing up from the target
    let dropZone = e.target;
    let foundDropZone = false;
    
    // Traverse up to find a valid drop zone (max 5 levels)
    for (let i = 0; i < 5 && dropZone; i++) {
        if (dropZone.classList && (
            dropZone.classList.contains('visible-stack') || 
            dropZone.classList.contains('battle-pile') ||
            dropZone.id === 'battlePile'
        )) {
            foundDropZone = true;
            break;
        }
        dropZone = dropZone.parentElement;
    }
    
    if (!foundDropZone || !dropZone) {
        console.log('handleDrop: No valid drop zone found');
        return;
    }
    
    dropZone.classList.remove('drag-over', 'valid-drop', 'invalid-drop');
    
    console.log('handleDrop called:', { draggedCard, dropZone: dropZone?.dataset, dropZoneId: dropZone?.id, dropZoneClasses: dropZone?.className });
    
    if (!draggedCard) {
        console.warn('handleDrop: No draggedCard');
        return;
    }

    if (gameState.phase === 'phase_two' && dropZone && (dropZone.id === 'battlePile' || dropZone.dataset.target === 'battle_pile')) {
        // Phase 2: playing card to battle pile
        console.log('Phase 2: Dropping on battle-pile');
        if (ws) {
            ws.send(JSON.stringify({
                action: 'play_card',
                card: {
                    suit: draggedCard.suit,
                    rank: draggedCard.rank
                }
            }));
        }
    } else if (dropZone && dropZone.classList.contains('visible-stack')) {
        const playerId = dropZone.dataset.playerId;
        console.log('Dropping on visible-stack:', { playerId, draggedCardSource: draggedCard.source });
        
        if (draggedCard.source === 'stack') {
            // Dragging from own stack to another player's stack
            // Don't allow giving to self - this would cause penalty
            if (playerId === gameState.player_id) {
                console.log('Cannot give from stack to own stack - ignoring drop');
                return;
            }
            
            console.log('Drag from stack to stack - sending give_from_stack');
            if (ws && gameState.phase === 'phase_one') {
                ws.send(JSON.stringify({
                    action: 'give_from_stack',
                    target_player_id: playerId
                }));
            }
        } else {
            // Dragging from hand to any stack
            console.log('Drag from hand to stack - calling placeCard');
            placeCard(draggedCard, playerId);
        }
    }
    
    // Clear dragged card immediately to prevent duplicate actions
    draggedCard = null;
    isDragging = false;
}

function handleDragEnd(e) {
    console.log('handleDragEnd called - drag operation finished');
    e.target.classList.remove('dragging');
    
    // Short delay to ensure drop event completes first
    setTimeout(() => {
        if (draggedCard) {
            console.log('Drag ended without drop - no action taken');
        }
        draggedCard = null;
        isDragging = false;
    }, 50);
    
    document.querySelectorAll('.drag-over, .valid-drop, .invalid-drop').forEach(el => {
        el.classList.remove('drag-over', 'valid-drop', 'invalid-drop');
    });
}

function canDropCard(card, dropZone) {
    if (!card) return false;
    
    if (dropZone.classList.contains('visible-stack')) {
        const playerId = dropZone.dataset.playerId;
        const player = gameState.players?.find(p => p.id === playerId);
        
        // Don't allow dropping on own stack when dragging from stack
        if (card.source === 'stack' && playerId === gameState.player_id) {
            return false;
        }
        
        if (!player || !player.visible_stack || player.visible_stack.length === 0) {
            return true;
        }
        
        const topCard = player.visible_stack[player.visible_stack.length - 1];
        // Seniority rule: 7→8, 8→9, etc. or 6→A
        if (card.rank === topCard.rank + 1) return true;
        if (card.rank === 6 && topCard.rank === 14) return true;
        
        return false;
    }
    
    if (dropZone.id === 'battlePile' || dropZone.classList.contains('battle-pile')) {
        return gameState.phase === 'phase_two';
    }
    
    return false;
}

// Game actions
function toggleReady() {
    if (ws && gameState.phase === 'waiting') {
        ws.send(JSON.stringify({ action: 'ready' }));
        
        const button = document.getElementById('readyButton');
        button.textContent = 'Ждем...';
        button.disabled = true;
    }
}

function drawCard() {
    if (ws && gameState.phase === 'phase_one') {
        const isMyTurn = gameState.players[gameState.current_player_index]?.id === gameState.player_id;
        
        if (isMyTurn) {
            ws.send(JSON.stringify({ action: 'draw_card' }));
        }
    }
}

function endTurn() {
    if (ws && gameState.phase === 'phase_one') {
        const isMyTurn = gameState.players[gameState.current_player_index]?.id === gameState.player_id;
        
        if (isMyTurn) {
            ws.send(JSON.stringify({ action: 'end_turn' }));
        }
    }
}

function placeCard(card, targetPlayerId) {
    console.log('Frontend placeCard called:', { card, targetPlayerId });
    
    if (ws && gameState.phase === 'phase_one') {
        const isMyTurn = gameState.players[gameState.current_player_index]?.id === gameState.player_id;
        
        if (isMyTurn) {
            const message = {
                action: 'place_card',
                card: card,
                target_player_id: targetPlayerId
            };
            console.log('Frontend sending message:', message);
            ws.send(JSON.stringify(message));
        }
    }
}

function placeCardOnOwnStack() {
    const myPlayer = gameState.players?.find(p => p.id === gameState.player_id);
    console.log('placeCardOnOwnStack - gameState.player_id:', gameState.player_id);
    console.log('placeCardOnOwnStack - myPlayer found:', myPlayer);
    console.log('placeCardOnOwnStack - all players:', gameState.players?.map(p => ({id: p.id, username: p.username})));
    
    if (myPlayer && myPlayer.hand && myPlayer.hand.length > 0) {
        const card = myPlayer.hand[0];
        console.log('placeCardOnOwnStack - card from hand:', card);
        console.log('placeCardOnOwnStack - sending to player ID:', gameState.player_id);
        placeCard(card, gameState.player_id);
    } else {
        console.log('placeCardOnOwnStack - no card in hand or no player found');
        console.log('placeCardOnOwnStack - myPlayer:', myPlayer);
        console.log('placeCardOnOwnStack - myPlayer.hand:', myPlayer?.hand);
    }
}

function beatCard(card) {
    if (ws && gameState.phase === 'phase_two') {
        ws.send(JSON.stringify({
            action: 'beat_card',
            card: card
        }));
    }
}

function takePile() {
    if (ws && gameState.phase === 'phase_two') {
        ws.send(JSON.stringify({ action: 'take_pile' }));
    }
}

// Helper functions
function getSuitSymbol(suit) {
    const symbols = {
        hearts: '♥',
        diamonds: '♦',
        clubs: '♣',
        spades: '♠'
    };
    return symbols[suit] || suit;
}

function getRankSymbol(rank) {
    console.log('getRankSymbol called with:', rank);
    if (rank === 11) return 'J';
    if (rank === 12) return 'Q';
    if (rank === 13) return 'K';
    if (rank === 14) return 'A';
    return rank.toString();
}

function showNotification(message, type = 'info') {
    const notification = document.getElementById('notification');
    notification.textContent = message;
    notification.className = `notification show ${type}`;
    
    setTimeout(() => {
        notification.classList.remove('show');
    }, 3000);
}

// Keyboard shortcuts
document.addEventListener('keydown', function(e) {
    if (e.key === 'r' && gameState.phase === 'waiting') {
        toggleReady();
    } else if (e.key === ' ' && gameState.phase === 'phase_one') {
        e.preventDefault();
        drawCard();
    } else if (e.key === 't' && gameState.phase === 'phase_two') {
        takePile();
    }
});

// Add new donation functions

function showDonationUI() {
    console.log('showDonationUI called');
    const myPlayer = gameState.players?.find(p => p.id === gameState.player_id);
    const isMyTurn = gameState.players && 
        gameState.players[gameState.current_player_index]?.id === gameState.player_id;
    
    console.log('showDonationUI check:', {
        isMyTurn,
        myPlayerHand: myPlayer?.hand?.length || 0,
        myPlayerBadCards: myPlayer?.bad_card_counter
    });
    
    if (!isMyTurn) {
        console.log('Not my turn, hiding donation UI');
        hideDonationUI();
        return;
    }
    
    // Check if there are players who need donations from ME (current player)
    // Use donation tracker to see if I've completed my donations to each recipient
    const playersNeedingCards = gameState.players.filter(p => {
        if (p.id === gameState.player_id) return false; // Exclude self
        if (p.bad_card_counter <= 0) return false; // No penalty, no need to donate
        
        // Check donation tracker to see how many cards I've donated to this player
        const recipientTracker = gameState.donation_tracker?.[p.id] || {};
        const myDonations = recipientTracker[gameState.player_id] || 0;
        const stillNeeded = p.bad_card_counter - myDonations;
        
        return stillNeeded > 0; // This player still needs cards from me
    });
    
    console.log('Players needing cards from me:', playersNeedingCards.map(p => ({
        username: p.username, 
        totalNeeds: p.bad_card_counter,
        alreadyDonated: (gameState.donation_tracker?.[p.id] || {})[gameState.player_id] || 0,
        stillNeeds: p.bad_card_counter - ((gameState.donation_tracker?.[p.id] || {})[gameState.player_id] || 0)
    })));
    
    // If no one needs cards from me or I have no cards, the backend should have skipped my turn
    // This should not happen, but just in case, hide the UI
    if (!myPlayer.hand || myPlayer.hand.length === 0 || playersNeedingCards.length === 0) {
        console.log('No donations needed from me - backend should have skipped this turn');
        hideDonationUI();
        return;
    }
    
    console.log('Starting donation process for', playersNeedingCards.length, 'recipients');
    // Start donation process with first player
    showDonationForPlayer(playersNeedingCards, 0);
}

function hideDonationUI() {
    const donationInterface = document.getElementById('donationInterface');
    if (donationInterface) {
        donationInterface.remove();
    }
}

let currentDonationRecipients = [];
let currentRecipientIndex = 0;
let allDonations = {}; // {targetPlayerId: [card_indices]}

function showDonationForPlayer(recipients, recipientIndex) {
    currentDonationRecipients = recipients;
    currentRecipientIndex = recipientIndex;
    
    if (recipientIndex >= recipients.length) {
        // All recipients processed - this shouldn't happen now since we send one at a time
        // But keep this as a safety check
        return;
    }
    
    const recipient = recipients[recipientIndex];
    const myPlayer = gameState.players?.find(p => p.id === gameState.player_id);
    
    if (!myPlayer || !myPlayer.hand || myPlayer.hand.length === 0) {
        // No cards to donate - send empty donation to skip this recipient
        if (ws) {
            ws.send(JSON.stringify({
                action: 'donate_cards',
                donations: {}
            }));
        }
        hideDonationUI();
        return;
    }
    
    // Check if we've already fully donated to this recipient
    // (based on game state's donation tracking)
    const donationTracker = gameState.donation_tracker || {};
    const recipientDonations = donationTracker[recipient.id] || {};
    const myDonations = recipientDonations[myPlayer.id] || 0;
    const stillNeeded = recipient.bad_card_counter - myDonations;
    
    if (stillNeeded <= 0) {
        // Already completed donations to this recipient, try next
        showDonationForPlayer(recipients, recipientIndex + 1);
        return;
    }
    
    // Show donation interface for this specific recipient
    let donationHTML = '<div id="donationInterface" class="donation-interface">';
    donationHTML += `<h3>Donate cards to ${recipient.username}</h3>`;
    donationHTML += `<p>They need ${stillNeeded} more card(s) from you</p>`;
    donationHTML += `<p>Select up to ${stillNeeded} cards from your hand:</p>`;
    
    donationHTML += '<div class="donation-my-stack">';
    donationHTML += myPlayer.hand.map((card, index) => {
        const isSelected = selectedDonationCards.has(index);
        return `<div class="card ${card.suit} ${isSelected ? 'selected-for-donation' : 'donation-selectable'}" 
                    onclick="toggleDonationCard(${index})"
                    data-card-index="${index}">
            <div class="rank rank-top-left">${getRankSymbol(card.rank)}</div>
            <div class="suit suit-top-left">${getSuitSymbol(card.suit)}</div>
            <div class="rank rank-bottom-right">${getRankSymbol(card.rank)}</div>
            <div class="suit suit-bottom-right">${getSuitSymbol(card.suit)}</div>
        </div>`;
    }).join('');
    donationHTML += '</div>';
    
    donationHTML += '<div class="donation-buttons">';
    donationHTML += '<button class="btn btn-primary" onclick="confirmDonationToPlayer()">Confirm</button>';
    donationHTML += '</div>';
    donationHTML += '</div>';
    
    // Remove old interface if exists
    const oldInterface = document.getElementById('donationInterface');
    if (oldInterface) oldInterface.remove();
    
    // Add to game board
    const gameBoard = document.querySelector('.game-board');
    gameBoard.insertAdjacentHTML('beforeend', donationHTML);
}

let selectedDonationCards = new Set();

function toggleDonationCard(cardIndex) {
    const recipient = currentDonationRecipients[currentRecipientIndex];
    const myPlayer = gameState.players?.find(p => p.id === gameState.player_id);
    
    // Calculate how many cards still needed
    const donationTracker = gameState.donation_tracker || {};
    const recipientDonations = donationTracker[recipient.id] || {};
    const myDonations = recipientDonations[myPlayer.id] || 0;
    const stillNeeded = recipient.bad_card_counter - myDonations;
    
    if (selectedDonationCards.has(cardIndex)) {
        selectedDonationCards.delete(cardIndex);
    } else {
        // Check if we haven't exceeded the limit
        if (selectedDonationCards.size < stillNeeded) {
            selectedDonationCards.add(cardIndex);
        } else {
            showNotification(`You can only select ${stillNeeded} more card(s) for this player`, 'error');
            return;
        }
    }
    
    // Update visual selection
    updateDonationCardSelection();
}

function updateDonationCardSelection() {
    const cards = document.querySelectorAll('.donation-selectable, .selected-for-donation');
    cards.forEach(card => {
        const index = parseInt(card.dataset.cardIndex);
        if (selectedDonationCards.has(index)) {
            card.classList.remove('donation-selectable');
            card.classList.add('selected-for-donation');
        } else {
            card.classList.remove('selected-for-donation');
            card.classList.add('donation-selectable');
        }
    });
}

function confirmDonationToPlayer() {
    const recipient = currentDonationRecipients[currentRecipientIndex];
    
    // Send donation for THIS recipient immediately
    const donationsForThisRecipient = {};
    if (selectedDonationCards.size > 0) {
        donationsForThisRecipient[recipient.id] = Array.from(selectedDonationCards);
    }
    
    if (ws) {
        ws.send(JSON.stringify({
            action: 'donate_cards',
            donations: donationsForThisRecipient
        }));
    }
    
    // Clear selection and wait for server to send updated game state
    selectedDonationCards.clear();
    hideDonationUI();
    
    // Don't immediately show next recipient - wait for server update
    // The server will send updated game state and showDonationUI() will be called again
}

function submitAllDonations() {
    // This function is no longer used but kept for compatibility
    // We now send donations one recipient at a time
    hideDonationUI();
}

function updateUI() {
    updatePhaseDisplay();
    updatePlayersDisplay();
    updateGameBoard();
    updateHand();
    updateGameActions();
    
    // Handle donation phase
    if (gameState.phase === 'donation') {
        const isMyTurn = gameState.players && 
            gameState.players[gameState.current_player_index]?.id === gameState.player_id;
        const currentPlayer = gameState.players && gameState.players[gameState.current_player_index];
        console.log('Donation phase:', {
            isMyTurn,
            currentPlayerUsername: currentPlayer?.username,
            myPlayerId: gameState.player_id,
            currentPlayerIndex: gameState.current_player_index
        });
        if (isMyTurn) {
            showDonationUI();
            hideWaitingModal();
        } else {
            hideDonationUI();
            showWaitingModal('Donation Phase', `Waiting for ${currentPlayer?.username} to donate cards...`);
        }
    } else {
        hideDonationUI();
        hideWaitingModal();
    }
    
    // Handle phase 2 UI
    if (gameState.phase === 'phase_two') {
        updatePhase2UI();
    }
}
// ============ Phase 2 Functions ============

function updatePhase2UI() {
    const battlePile = document.getElementById('battlePile');
    const gameActions = document.getElementById('gameActions');
    
    if (!battlePile) {
        console.warn('updatePhase2UI: battlePile element not found');
        return;
    }
    
    const isMyTurn = gameState.players && 
        gameState.players[gameState.current_player_index]?.id === gameState.player_id;
    
    const myPlayer = gameState.players?.find(p => p.id === gameState.player_id);
    
    // Remove old event listeners first to avoid duplicates
    battlePile.removeEventListener('dragover', handleDragOver);
    battlePile.removeEventListener('drop', handleDrop);
    
    // Make battle pile droppable during phase 2 when it's player's turn
    if (isMyTurn && myPlayer && !myPlayer.is_out) {
        console.log('Setting up battle pile as drop zone');
        battlePile.classList.add('drop-zone-active');
        battlePile.addEventListener('dragover', handleDragOver);
        battlePile.addEventListener('drop', handleDrop);
        battlePile.dataset.target = 'battle_pile';
    } else {
        battlePile.classList.remove('drop-zone-active');
        battlePile.removeAttribute('data-target');
    }
    
    // Show phase 2 actions
    if (isMyTurn && myPlayer && !myPlayer.is_out && gameActions) {
        gameActions.classList.remove('hidden');
        
        // Hide Phase 1 buttons
        const drawButton = document.getElementById('drawButton');
        const endTurnButton = document.getElementById('endTurnButton');
        if (drawButton) drawButton.style.display = 'none';
        if (endTurnButton) endTurnButton.style.display = 'none';
        
        // Clear any existing Phase 2 content first
        const existingPhase2 = gameActions.querySelector('.phase2-actions');
        if (existingPhase2) {
            existingPhase2.remove();
        }
        
        // Create Phase 2 actions container
        const phase2Container = document.createElement('div');
        phase2Container.className = 'phase2-actions';
        
        // Show "Take Pile" button if battle pile exists
        if (gameState.battle_pile && gameState.battle_pile.length > 0) {
            const takePileBtn = document.createElement('button');
            takePileBtn.className = 'btn btn-secondary';
            takePileBtn.textContent = 'Взять';
            takePileBtn.onclick = takeBattlePile;
            phase2Container.appendChild(takePileBtn);
        }
        
        // Show instruction
        const instruction = document.createElement('p');
        instruction.style.color = 'white';
        instruction.style.marginTop = '10px';
        if (!gameState.battle_pile || gameState.battle_pile.length === 0) {
            instruction.textContent = 'Положи карту, щоб почати битву';
        } else {
            instruction.textContent = 'Перетягніть карту, щоб побити верхню карту, або візьміть купу';
        }
        phase2Container.appendChild(instruction);
        
        gameActions.appendChild(phase2Container);
    } else if (gameActions) {
        gameActions.classList.add('hidden');
    }
}

function playCardToPhase2(card) {
    if (!ws) return;
    
    ws.send(JSON.stringify({
        action: 'play_card',
        card: card
    }));
}

function takeBattlePile() {
    console.log('takeBattlePile called');
    if (!ws) {
        console.error('WebSocket not connected');
        return;
    }
    
    const message = {
        action: 'take_pile'
    };
    
    console.log('Sending take_pile action:', message);
    ws.send(JSON.stringify(message));
    
    showNotification('Taking battle pile...', 'info');
}

// Waiting Modal Functions
function showWaitingModal(title, message) {
    const modal = document.getElementById('waitingModal');
    const titleElement = document.getElementById('waitingModalTitle');
    const messageElement = document.getElementById('waitingModalMessage');
    
    if (modal && titleElement && messageElement) {
        titleElement.textContent = title;
        messageElement.textContent = message;
        modal.classList.remove('hidden');
    }
}

function hideWaitingModal() {
    const modal = document.getElementById('waitingModal');
    if (modal) {
        modal.classList.add('hidden');
    }
}

// Game End Modal Functions
function showGameEndModal(loserName) {
    console.log('showGameEndModal called with loserName:', loserName);
    
    const modal = document.getElementById('gameEndModal');
    const loserElement = document.getElementById('gameEndLoser');
    const messageElement = document.getElementById('gameEndMessage');
    
    console.log('Modal elements found:', {
        modal: !!modal,
        loserElement: !!loserElement,
        messageElement: !!messageElement
    });
    
    if (modal && loserElement && messageElement) {
        loserElement.textContent = `${loserName} ПРОІГРАВ! 🤡`;
        messageElement.textContent = `${loserName} отримає +1 приховану карту штрафу в наступному раунді. Натисніть Готов, щоб грати знову!`;
        modal.classList.remove('hidden');
        console.log('Game end modal displayed');
    } else {
        console.error('Game end modal elements not found!');
    }
}

function closeGameEndModal() {
    const modal = document.getElementById('gameEndModal');
    if (modal) {
        modal.classList.add('hidden');
    }
}
