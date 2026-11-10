# Music subsystem notices

The music subsystem's feature layout and playback workflow were informed by **The-HellBot/Music** (The-HellBot; HellBoy-OP and ProRaiden), available at https://github.com/The-HellBot/Music and published under **GNU GPL version 3 or later**.

MikasaVerse uses a separate Python Telegram Bot + PyrogramMod + PyTgCalls 2.3.3 architecture. These modules are compatibility adapters and an independently written integration; the legacy HellBot Pyrogram plugin files are not copied wholesale because their older PyTgCalls API is incompatible with this project.

The streaming flow uses local audio files with PyTgCalls' file-path input, following the local-file playback approach documented by HellBot. Upstream project attribution is retained here.
