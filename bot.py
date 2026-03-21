<!DOCTYPE html>
<html lang="am">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
    <title>BESH BINGO - PREMIUM</title>
    <style>
        :root { --gold: #ffcc00; --bg: #050a18; --red: #ff3e3e; --green: #00ff88; --orange: #ff8800; }
        body { background: var(--bg); color: white; font-family: sans-serif; text-align: center; margin: 0; padding: 0; }
        .header-banner { background: #1e293b; padding: 15px; border-bottom: 3px solid var(--gold); }
        .stats { display: flex; justify-content: space-around; background: rgba(255,255,255,0.05); padding: 10px; margin: 10px; border-radius: 12px; }
        .stats b { display: block; color: var(--gold); }
        .grid-500 { display: grid; grid-template-columns: repeat(10, 1fr); gap: 4px; max-height: 200px; overflow-y: auto; background: #000; padding: 10px; margin: 10px; border-radius: 10px; }
        .t-box { background: #fff; color: #000; font-size: 0.7rem; padding: 8px 0; font-weight: bold; border-radius: 4px; cursor: pointer; }
        .t-box.mine { background: var(--gold); border: 2px solid white; }
        .t-box.sold { background: #333; color: #666; cursor: not-allowed; }
        #ball-circle { width: 80px; height: 80px; background: radial-gradient(circle, #fff, var(--gold)); color: #000; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 2rem; font-weight: 900; margin: 15px auto; border: 4px solid #1e293b; }
        .bingo-card { display: grid; grid-template-columns: repeat(5, 1fr); gap: 2px; background: #444; padding: 4px; border-radius: 0 0 8px 8px; }
        .cell { background: #fff; color: #000; height: 45px; display: flex; align-items: center; justify-content: center; font-weight: 900; font-size: 1.2rem; cursor: pointer; }
        .cell.selected { background: var(--gold) !important; border: 2px solid #b45309; }
        .cell.free { background: #ff3e3e !important; color: white; font-size: 0.7rem; }
        #bingo-btn { width: 80%; padding: 15px; background: red; color: white; font-size: 1.5rem; font-weight: 900; border-radius: 10px; margin: 10px; border:none; }
        .modal { display:none; position:fixed; inset:0; background:rgba(0,0,0,0.9); z-index:2000; align-items:center; justify-content:center; }
    </style>
</head>
<body>
    <audio id="win-horn" src="https://assets.mixkit.co/active_storage/sfx/1435/1435-preview.mp3" preload="auto"></audio>

    <div class="header-banner"><h1>BESH BINGO</h1></div>

    <div class="stats">
        <div>BALANCE<b id="balance">0 ETB</b></div>
        <div>PLAYERS<b id="p-count">0</b></div>
        <div>JACKPOT<b id="prize">0 ETB</b></div>
    </div>

    <div id="lobby-ui">
        <div id="timer" style="font-size: 2rem; color: var(--gold);">30</div>
        <p>ካርቴላ ለመግዛት ቁጥር ይንኩ (10 ETB)</p>
        <div class="grid-500" id="ticket-area"></div>
    </div>

    <div id="game-ui" style="display:none;">
        <div id="ball-circle">--</div>
        <div id="card-area" style="padding:10px;"></div>
        <button id="bingo-btn" onclick="claim()">BINGO!</button>
    </div>

    <div id="result-ui" class="modal">
        <div style="text-align:center;">
            <h1 style="color:var(--gold); font-size:3rem;">BINGO!</h1>
            <div id="winner-info" style="font-size:2rem; color:white;"></div>
        </div>
    </div>

    <script>
        let phone = localStorage.getItem('phone') || prompt("ስልክ ቁጥር ያስገቡ:");
        let username = localStorage.getItem('username') || prompt("የተጫዋች ስም ያስገቡ:");
        if(!phone || !username) location.reload();
        localStorage.setItem('phone', phone); localStorage.setItem('username', username);

        let currentCardData = null; 
        let announced = false;
        let lastBall = "--";
        let validToMark = [];

        function speakBall(ball) {
            if (!ball || ball === "--") return;
            let utterance = new SpeechSynthesisUtterance(ball.replace(/([A-Z])(\d+)/, '$1 $2'));
            utterance.lang = 'en-US'; utterance.rate = 0.8;
            window.speechSynthesis.speak(utterance);
        }

        function update() {
            fetch(`/get_status?phone=${phone}`).then(r=>r.json()).then(d=>{
                document.getElementById('balance').innerText = d.balance.toFixed(0) + " ETB";
                document.getElementById('prize').innerText = Math.floor(d.pot * 0.8) + " ETB";
                document.getElementById('p-count').innerText = d.active_players;
                document.getElementById('timer').innerText = d.timer;
                validToMark = d.valid_to_mark || [];

                if(d.status === "lobby") {
                    document.getElementById('lobby-ui').style.display='block'; 
                    document.getElementById('game-ui').style.display='none'; 
                    document.getElementById('result-ui').style.display='none';
                    currentCardData = null; announced = false; lastBall = "--";
                    renderTickets(d.sold_tickets);
                } else if(d.status === "playing") {
                    document.getElementById('lobby-ui').style.display='none'; 
                    document.getElementById('game-ui').style.display='block';
                    document.getElementById('ball-circle').innerText = d.current_ball;
                    
                    if (d.current_ball !== lastBall) {
                        lastBall = d.current_ball; speakBall(lastBall);
                    }

                    if(d.my_cards.length > 0 && !currentCardData) { 
                        currentCardData = d.my_cards; 
                        document.getElementById('card-area').innerHTML = "";
                        d.my_cards.forEach((c, i) => renderCard(c, i)); 
                    }
                } else if(d.status === "result") {
                    document.getElementById('result-ui').style.display='flex';
                    document.getElementById('winner-info').innerText = d.winner + " 🏆";
                    if(!announced) { document.getElementById('win-horn').play(); announced = true; }
                }
            });
        }

        function renderTickets(sold) {
            const area = document.getElementById('ticket-area');
            if(area.children.length === 0) {
                for(let i=1; i<=500; i++) { 
                    let div=document.createElement('div'); div.className='t-box'; div.innerText=i; div.id='t-'+i;
                    area.appendChild(div); 
                }
            }
            for(let i=1; i<=500; i++) {
                let box = document.getElementById('t-'+i);
                if(sold[i]) {
                    box.className = (sold[i] === phone) ? 't-box mine' : 't-box sold';
                    box.onclick = (sold[i] === phone) ? () => refund(i) : null;
                } else { 
                    box.className='t-box'; box.onclick=()=>buy(i); 
                }
            }
        }

        function buy(n) { fetch('/buy_specific_ticket', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({phone, ticket_num:n, username})}).then(update); }
        
        function refund(n) { 
            if(confirm(n + " ቁጥርን መልሰው 10 ብር እንዲመለስልዎት ይፈልጋሉ?")) 
                fetch('/return_ticket', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({phone, ticket_num:n})}).then(update); 
        }

        function renderCard(card, cardIdx) {
            const wrap = document.createElement('div'); wrap.style.marginBottom="15px";
            wrap.innerHTML = `<div style="display:grid;grid-template-columns:repeat(5,1fr);background:var(--orange);font-weight:900;padding:5px;border-radius:8px 8px 0 0;"><div>B</div><div>I</div><div>N</div><div>G</div><div>O</div></div>`;
            const grid = document.createElement('div'); grid.className='bingo-card';
            card.forEach((n, i) => {
                const cell=document.createElement('div');
                if(i === 12) { cell.className="cell free selected"; cell.innerText="FREE"; }
                else {
                    cell.className="cell"; cell.innerText=n;
                    cell.onclick = () => { 
                        let validNums = validToMark.map(b => parseInt(b.substring(1)));
                        if (!cell.classList.contains('selected') && !validNums.includes(n)) {
                            alert("ይህ ቁጥር አልፎበታል! ማቅለም አይችሉም።"); return;
                        }
                        cell.classList.toggle('selected'); 
                    };
                }
                grid.appendChild(cell);
            });
            wrap.appendChild(grid); document.getElementById('card-area').appendChild(wrap);
        }

        function claim() { 
            fetch('/claim_bingo', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({phone})})
            .then(r=>r.json()).then(d=>{ if(!d.success) alert(d.msg); }); 
        }

        setInterval(update, 2000);
        update();
    </script>
</body>
</html>
