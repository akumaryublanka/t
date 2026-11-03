<!-- ━━━━━━━━━━━━━━━━━━━━━━━ HERO ━━━━━━━━━━━━━━━━━━━━━━━ -->

<div align="center">

  <img src="https://capsule-render.vercel.app/api?type=waving&color=0:120D2E,45:6C3B8E,100:F4A7C8&height=220&section=header&text=AdamWaifusbot&fontSize=56&fontColor=FFFFFF&fontAlignY=38&desc=Collect%20your%20favorite%20anime%20characters&descAlignY=60&descSize=16" width="100%" alt="AdamWaifusbot banner" />

  <p><b>✦ Catch · Collect · Trade · Compete ✦</b></p>

  <p>
    <a href="https://github.com/gagancodeX/AdamWaifusbot"><img src="https://img.shields.io/badge/Python-3.10%2B-8B7CF6?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.10+"/></a>
    <a href="https://python-telegram-bot.org/"><img src="https://img.shields.io/badge/Telegram_Bot-21.9-2AABEE?style=for-the-badge&logo=telegram&logoColor=white" alt="python-telegram-bot 21.9"/></a>
    <a href="https://www.mongodb.com/"><img src="https://img.shields.io/badge/MongoDB-Atlas-47A248?style=for-the-badge&logo=mongodb&logoColor=white" alt="MongoDB Atlas"/></a>
    <a href="https://render.com/"><img src="https://img.shields.io/badge/Deploy-Render-7C3AED?style=for-the-badge&logo=render&logoColor=white" alt="Render"/></a>
  </p>

  <p>
    <a href="#-features">Features</a> ·
    <a href="#-getting-started">Setup</a> ·
    <a href="#-commands">Commands</a> ·
    <a href="#-configuration">Configuration</a> ·
    <a href="#-security">Security</a>
  </p>

</div>

---

## 🌙 The world of AdamWaifusbot

**AdamWaifusbot** is an anime character collector for Telegram groups. Characters appear in chat, players race to guess their names, and every successful catch grows their collection. Earn coins and XP, hunt rare drops, trade with friends, challenge rivals, and climb the leaderboard.

> *Your next favorite character could drop at any moment. Stay ready.*

## ✨ Features

<table>
  <tr>
    <td width="50%">
      <h3>🎴 Catch & Collect</h3>
      Random character drops, name guessing, favorites, paginated collections, duplicate counts, and inline search.
    </td>
    <td width="50%">
      <h3>💎 Rarity & Progression</h3>
      XP, levels, profile titles, rarity statistics, collection value, and special character drops.
    </td>
  </tr>
  <tr>
    <td width="50%">
      <h3>💰 Economy & Market</h3>
      Daily coins, redeem codes, character listings, purchases, selling, and delisting.
    </td>
    <td width="50%">
      <h3>⚔️ Social & Competition</h3>
      PvP duels, trades, gifts, marriage, daily couples, and leaderboards.
    </td>
  </tr>
  <tr>
    <td width="50%">
      <h3>🧩 Mini-games</h3>
      Mines, image quizzes, and typed name-guessing rounds.
    </td>
    <td width="50%">
      <h3>🛠️ Admin Toolkit</h3>
      Character management, imports, group drop settings, broadcasts, and maintenance mode.
    </td>
  </tr>
</table>

## 🎨 Rarity guide

The /upload command uses these rarity numbers:

| Number | Rarity |
|:---:|---|
| 1 | ⚪ Common |
| 2 | 🟢 Medium |
| 3 | 🟣 Rare |
| 4 | 🔵 Epic |
| 5 | 🟡 Legendary |
| 6 | 🌟 Luminous |
| 7 | 🎐 Celestial |
| 8 | 💮 Special Edition |
| 9 | 🔮 Limited Edition |
| 10 | 🔞 Extreme |
| 11 | ❄️ Winter |
| 12 | ☀️ Summer |
| 13 | 🏅 Elite |
| 14 | 🎭 Cosplay |
| 15 | 🎃 Halloween |
| 16 | 💝 Valentine's |

### Main rarity drop weights

| Tier | Character rarity | Base drop weight |
|:---:|---|---:|
| ⚪ | Common | 40 |
| 🟢 | Medium | 25 |
| 🟣 | Rare | 20 |
| 🔵 | Epic | 12 |
| 🟡 | Legendary | 8 |
| 🌟 | Luminous | 5 |
| 🎐 | Celestial | 3 |
| 💮 | Special Edition | 2 |
| 🔮 | Limited Edition | 1 |

Lower drop weights generally mean rarer drops. Owner-flagged special characters may have additional drop behavior. Actual results depend on the character catalogue and implementation.

## 🪙 Game economy

| Action | Reward / cost |
|---|---:|
| `/daily` | **+200 coins** · 24-hour cooldown |
| Duel victory | **+150 coins + 100 XP** |
| Duel defeat | **+30 coins + 25 XP** |
| Correct character guess | **+50 XP** |
| Start `/mines` | **−500 coins** |

Collection values are estimated by rarity. Mines uses a 4×4 board with 3 mines and 2 lives; multipliers rise as safe tiles are found. Play responsibly with your in-game coins.

## 🚀 Getting started

### Requirements

- Python **3.10+**
- A Telegram bot token from [@BotFather](https://t.me/BotFather)
- A MongoDB database (MongoDB Atlas works well)
- A Telegram group for the bot and a channel for character posts
- Render account if deploying with the included HTTP keep-alive service

### 1 · Clone

```bash
git clone https://github.com/gagancodeX/AdamWaifusbot.git
cd AdamWaifusbot
```

### 2 · Install dependencies

```bash
python -m pip install -r requirements.txt
```

### 3 · Create your environment file

Create a `.env` file in the project root and fill in your own values:

```env
BOT_TOKEN=your_telegram_bot_token
BOT_USERNAME=your_bot_username_without_at
OWNER_ID=your_telegram_user_id
SUDO_IDS=123456789,987654321
GROUP_ID=-1001234567890
CHARA_CHANNEL_ID=-1001234567890
MONGO_URI=your_mongodb_connection_string
DB_NAME=waifu_bot
SUPPORT_CHAT=your_support_chat_username
UPDATE_CHAT=your_updates_channel_username
PHOTO_URLS=https://example.com/image1.jpg,https://example.com/image2.jpg
DROP_INTERVAL_MINUTES=15
DEFAULT_MSG_FREQUENCY=20
```

### 4 · Launch

```bash
python -m waifu
```

<div align="center">
  <sub>Once configured, add the bot to your group and grant the permissions it needs to post and manage messages.</sub>
</div>

## ⚙️ Configuration

| Variable | Required? | Description |
|---|:---:|---|
| `BOT_TOKEN` | Yes | Telegram bot token from BotFather |
| `BOT_USERNAME` | Yes | Username without `@` |
| `OWNER_ID` | Yes | Owner's Telegram numeric user ID |
| `SUDO_IDS` | No | Comma-separated extra sudo IDs |
| `GROUP_ID` | Yes | Log / notification group ID |
| `CHARA_CHANNEL_ID` | Yes | Character-post channel ID |
| `MONGO_URI` | Yes | MongoDB connection string |
| `DB_NAME` | No | Database name; default: `waifu_bot` |
| `SUPPORT_CHAT` | No | Support chat username |
| `UPDATE_CHAT` | No | Updates channel username |
| `PHOTO_URLS` | No | Comma-separated fallback image URLs |
| `DROP_INTERVAL_MINUTES` | No | Timed drop interval; default: `15` |
| `DEFAULT_MSG_FREQUENCY` | No | Messages between drops; default: `20` |

The module loader may also support `LOAD_MODULES` and `NO_LOAD_MODULES`, depending on the current implementation. Several mini-games are restricted to a configured group ID in code.

## 🎮 How a catch works

```text
Group chat activity
       │
       ▼
Character drop appears
       │
       ▼
Player guesses with /guess
       │
       ▼
Character added to collection
       │
       ├── Earn XP
       ├── Build your harem
       ├── Trade or sell copies
       └── Climb the leaderboard
```

Drops can be triggered by group message counts or a scheduled interval. The bot uses rarity-weighted selection and a recent-character window to help reduce repeats. Unclaimed drops expire after 10 minutes; some game and drop state is held in memory and can reset when the process restarts.

## 📖 Commands

Commands may require a reply, group context, or owner/sudo permissions. Availability can vary with configuration.

<details>
<summary><b>🎴 Collection & profiles</b></summary>

| Command | What it does |
|---|---|
| `/start` | Open the welcome screen and help panel |
| `/guess` | Catch the active character |
| `/protecc`, `/collect`, `/grab`, `/hunt` | Guess aliases |
| `/harem`, `/collection` | Browse your collection |
| `/profile` | View XP, level, and collection stats |
| `/fav id` | Set a favorite character |
| `/rarity` | See rarity counts and estimated value |
| `/top`, `/ctop`, `/TopGroups` | View leaderboards |

</details>

<details>
<summary><b>💰 Economy & mini-games</b></summary>

| Command | What it does |
|---|---|
| `/daily`, `/balance` | Claim daily coins and check balance |
| `/sell`, `/market`, `/buy`, `/delist` | Marketplace actions |
| `/redeem CODE` | Redeem a code |
| `/mines`, `/cashout` | Play Mines and cash out |
| `/quiz [easy\|medium\|hard]` | Start the image quiz |
| `/qlb` | Quiz leaderboard |
| `/nguess`, `/nguess_stop` | Start or stop name guessing |
| `/hclaim` | Claim a free character on cooldown |
| `/couple` | Daily group couple |

</details>

<details>
<summary><b>🤝 Social & battles</b></summary>

| Command | What it does |
|---|---|
| `/duel` | Challenge another user (reply) |
| `/trade` | Propose a character trade |
| `/gift` | Gift a character |
| `/marry` | Propose marriage (reply) |
| `/spouse` | View spouse information |
| `/divorce` | End a marriage |

</details>

<details>
<summary><b>🛠️ Admin & owner tools</b></summary>

| Command | What it does |
|---|---|
| `/upload`, `/uploadchar` | Upload characters |
| `/delete`, `/update` | Manage catalogue entries |
| `/importjson`, `/resetchars` | Import or reset catalogue data |
| `/gencode`, `/codes`, `/delcode` | Manage redeem codes |
| `/setspecial`, `/removespecial`, `/speciallist` | Manage special drops |
| `/changetime n`, `/resettime` | Adjust group drop frequency |
| `/broadcast` | Broadcast a message |
| `/maintenance on\|off` | Toggle maintenance mode |
| `/resetall` | Reset player progress |
| `/ping` | Check latency and uptime |
| `/e`, `/py`, `/sh`, `/clearlocals` | Developer tools — highly privileged |

</details>

## 🧱 Built with

<p>
  <img src="https://img.shields.io/badge/Python-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python"/>
  <img src="https://img.shields.io/badge/Telegram_Bot_API-26A5E4?style=flat-square&logo=telegram&logoColor=white" alt="Telegram Bot API"/>
  <img src="https://img.shields.io/badge/MongoDB-47A248?style=flat-square&logo=mongodb&logoColor=white" alt="MongoDB"/>
  <img src="https://img.shields.io/badge/AsyncIO-222222?style=flat-square&logo=python&logoColor=white" alt="AsyncIO"/>
  <img src="https://img.shields.io/badge/Render-7C3AED?style=flat-square&logo=render&logoColor=white" alt="Render"/>
</p>

- **Bot framework:** python-telegram-bot 21.9
- **Database:** MongoDB via Motor and PyMongo
- **Scheduling:** APScheduler
- **HTTP service:** aiohttp
- **Caching / config:** cachetools and python-dotenv

## 🗄️ Data storage

The bot uses MongoDB collections for the character catalogue, user collections and balances, group settings and statistics, market listings, redeem codes, quiz scores, cooldowns, maintenance settings, and character ID sequences.

## ☁️ Deploying to Render

1. Create a Render web service connected to this repository.
2. Add the required environment variables in the Render dashboard.
3. Configure MongoDB Atlas network access and credentials for your service.
4. Use the project's supported Python start command: `python -m waifu`.
5. Check Render logs and the `/health` endpoint when troubleshooting.

The included keep-alive server is intended to bind to `0.0.0.0` and Render's `PORT` environment variable (with a local fallback of `8080`).

## 🔐 Security first

- Never commit `.env`, Telegram tokens, or database credentials.
- If a secret was committed, rotate it immediately; deleting the file in a later commit does not erase Git history.
- Treat `/eval` and `/sh` as server-level access. Only grant these tools to operators you fully trust.
- Review hardcoded IDs and move deployment-specific settings into environment variables where possible.
- Back up MongoDB before imports, catalogue resets, or progress wipes.
- In-memory game sessions can be lost on restart.
- Economy operations should use atomic database updates to prevent race conditions and duplicate payouts.

## 🧪 Known issues to review

These are audit notes to verify against the current code and deployment—not a guarantee that every issue reproduces in every environment.

- Check atomicity in redeem, purchase, trade, gift, and cash-out flows.
- Verify imported character records always receive the IDs expected by other commands.
- Review async handler blocking and timeout behavior in marriage and daily-claim flows.
- Check whether help text references commands without registered handlers.
- Ensure duel power calculation supports every rarity, including Extreme.
- Consider caching or querying a smaller sample as the character catalogue grows.
- Move hardcoded owner/group IDs into configuration.
- Review repository history and ignore rules for accidental secrets.
- Confirm module auto-loading and environment overrides match the intended behavior.

## 🤝 Contributing

Ideas, bug reports, and pull requests are welcome.

1. Fork the repository.
2. Create a branch for your change.
3. Test your changes.
4. Open a pull request with a clear summary.

## 📄 License

See the repository's `LICENSE` file for the applicable license terms.

<div align="center">

  <br/>

  <img src="https://capsule-render.vercel.app/api?type=waving&color=0:F4A7C8,50:6C3B8E,100:120D2E&height=120&section=footer" width="100%" alt="Decorative footer"/>

  <b>🌸 Made for anime fans, collectors, and late-night group chats 🌸</b>

  <p><sub>Catch your favorites. Build your collection. Own the leaderboard.</sub></p>

</div>
