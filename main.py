import discord
from discord.ext import commands
import os
from dotenv import load_dotenv
import yt_dlp
from collections import deque
import asyncio
import traceback
import random
import json
from yt_dlp import YoutubeDL
import platform
import psutil
from pymongo import MongoClient
import datetime


# --------------------------
# Configuración Inicial
# --------------------------

# --------------------------
# Configuración Inicial
# --------------------------

load_dotenv()  # Primero cargar variables de entorno

MONGO_URI = os.getenv("MONGO_URI")
if not MONGO_URI:
    raise ValueError("No se encontró MONGO_URI en las variables de entorno")

try:
    mongo_client = MongoClient(MONGO_URI)
    # Verificar conexión
    mongo_client.admin.command('ping')
    print("✅ Conexión a MongoDB establecida")
    
    db = mongo_client["discord_music_bot"]
    playlists_collection = db["playlists"]
    # Crear índice único para evitar duplicados
    playlists_collection.create_index([("guild_id", 1), ("name", 1)], unique=True)
    
except Exception as e:
    print(f"❌ Error al conectar a MongoDB: {e}")
    raise

# Resto del código...


intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Configuración de audio
FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -probesize 32M -analyzeduration 32M -fflags +discardcorrupt',
    'options': '-vn -c:a libopus -b:a 192k -ar 48000 -ac 2 -af "loudnorm=I=-16:TP=-1.5:LRA=11:measured_I=-16:measured_TP=-1.5:measured_LRA=11:measured_thresh=-30:offset=0,acompressor=threshold=-20dB:ratio=4:attack=50:release=200:makeup=3" -application lowdelay',
    'executable': 'ffmpeg',
}

# Configuración optimizada para yt-dlp
def get_ydl_opts():
    return {
        'format': 'bestaudio/best',
        'quiet': True,
        'no_warnings': True,
        'extractaudio': True,
        'audioformat': 'opus',
        'noplaylist': True,
        'socket_timeout': 10,
        'source_address': '0.0.0.0',
        'retries': 3,
        'extractor_args': {
            'youtube': {
                'skip': ['dash', 'hls'],
                'player_client': ['android'],
                'player_skip': ['configs', 'webpage']
            }
        },
        'referer': 'https://www.youtube.com',
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'postprocessor_args': {
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'opus',
            'preferredquality': '192'
        }
    }

# Ajustar límites del sistema
def increase_file_limits():
    """Aumenta los límites de archivos abiertos si es posible"""
    if platform.system() != 'Windows':
        try:
            import resource
            soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
            resource.setrlimit(resource.RLIMIT_NOFILE, (8192, hard))
        except (ImportError, ValueError, OSError) as e:
            print(f"No se pudo ajustar límites de archivos: {e}")
    else:
        print("En Windows, los límites de archivos se manejan automáticamente")

increase_file_limits()

# --------------------------
# Sistema de Colas
# --------------------------

class MusicQueue:
    def __init__(self):
        self.queues = {}
        self.current = {}
        self.is_playing = {}

    def get_queue(self, guild_id: int) -> deque:
        if guild_id not in self.queues:
            self.queues[guild_id] = deque()
            self.is_playing[guild_id] = False
        return self.queues[guild_id]

    def clear(self, guild_id: int):
        if guild_id in self.queues:
            self.queues[guild_id].clear()
        if guild_id in self.current:
            del self.current[guild_id]
        if guild_id in self.is_playing:
            self.is_playing[guild_id] = False

    def set_playing(self, guild_id: int, status: bool):
        self.is_playing[guild_id] = status

    def get_playing(self, guild_id: int) -> bool:
        return self.is_playing.get(guild_id, False)

music_queue = MusicQueue()
# --------------------------
# Clase del Reproductor
# --------------------------

class MusicPlayer:
    YDL_OPTIONS = {
        'format': 'bestaudio/best',
        'quiet': True,
        'no_warnings': True,
        'extractaudio': True,
        'audioformat': 'mp3',
        'noplaylist': True,
    }

    @classmethod
    async def get_audio_source(cls, query: str) -> dict:
        try:
            ydl_opts = get_ydl_opts()
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                if not query.startswith(('http://', 'https://')):
                    query = f"ytsearch:{query}"
                
                info = await bot.loop.run_in_executor(None, lambda: ydl.extract_info(query, download=False))
                
                if 'entries' in info:
                    info = info['entries'][0]
                
                youtube_url = f"https://youtu.be/{info['id']}" if 'id' in info else query
                
                return {
                    'url': info['url'],
                    'youtube_url': youtube_url,
                    'title': info.get('title', 'Audio desconocido'),
                    'duration': info.get('duration', 0),
                    'thumbnail': info.get('thumbnail', 'https://i.imgur.com/8QZQZ.png'),
                    'requested_by': 'Solicitado'
                }
        except Exception as e:
            print(f"Error al obtener audio: {traceback.format_exc()}")
            return None




@bot.command(name="renamepl")
async def rename_playlist(ctx, *, argumentos: str):
    try:
        nombre_actual, nuevo_nombre = map(str.strip, argumentos.split("|", 1))
    except ValueError:
        return await ctx.send("❌ Formato incorrecto. Usa: `!renamepl nombre_actual | nuevo_nombre`")

    guild_id = str(ctx.guild.id)
    playlist = playlists_collection.find_one({"guild_id": guild_id, "name": nombre_actual})
    if not playlist:
        return await ctx.send("❌ No se encontró esa playlist para renombrar.")

    if playlists_collection.find_one({"guild_id": guild_id, "name": nuevo_nombre}):
        return await ctx.send("⚠️ Ya existe una playlist con ese nombre.")

    playlists_collection.delete_one({"guild_id": guild_id, "name": nombre_actual})
    playlist["name"] = nuevo_nombre
    playlists_collection.insert_one(playlist)

    embed = discord.Embed(
        title="✏️ Playlist renombrada",
        description=f"**{nombre_actual}** fue renombrada exitosamente a **{nuevo_nombre}**.",
        color=discord.Color.green()
    )
    await ctx.send(embed=embed)


@bot.command(name="savepl")
async def save_playlist(ctx, *, nombre: str):
    guild_id = str(ctx.guild.id)
    queue = [c.copy() for c in music_queue.get_queue(ctx.guild.id)]
    current = music_queue.current.get(ctx.guild.id)
    canciones = [current.copy()] if current else []
    canciones.extend(queue)

    if not canciones:
        return await ctx.send("❌ No hay música en reproducción ni en cola para guardar.")

    try:
        playlists_collection.update_one(
            {"guild_id": guild_id, "name": nombre},
            {"$set": {
                "guild_id": guild_id,
                "name": nombre,
                "songs": canciones,
                "saved_at": datetime.datetime.utcnow(),
                "saved_by": ctx.author.display_name
            }},
            upsert=True
        )
        await ctx.send(f"✅ Playlist **{nombre}** guardada con {len(canciones)} canciones.")
    except Exception as e:
        return await ctx.send(f"❌ Error al guardar la playlist: `{e}`")

@bot.command(name="loadpl")
async def load_playlist(ctx, *, nombre: str):
    guild_id = str(ctx.guild.id)
    resultado = playlists_collection.find_one({"guild_id": guild_id, "name": nombre})

    if not resultado:
        return await ctx.send("❌ No se encontró esa playlist.")

    lista_guardada = resultado.get("songs", [])
    if not lista_guardada:
        return await ctx.send("ℹ️ La playlist está vacía.")

    queue = music_queue.get_queue(ctx.guild.id)
    total = len(lista_guardada)
    exitosas = 0

    msg = await ctx.send(f"🔄 Cargando playlist ({total} canciones)...")

    for cancion in lista_guardada:
        try:
            # Usar youtube_url si existe, si no usar title como búsqueda
            query = cancion.get('youtube_url', cancion.get('title', ''))
            if not query:
                continue
                
            datos = await MusicPlayer.get_audio_source(query)
            if datos:
                # Preservar metadatos originales
                datos.update({
                    'requested_by': cancion.get('requested_by', ctx.author.display_name),
                    'request_channel_id': ctx.channel.id,
                    'duration': cancion.get('duration', datos.get('duration', 0)),
                    'thumbnail': cancion.get('thumbnail', datos.get('thumbnail'))
                })
                queue.append(datos)
                exitosas += 1
        except Exception as e:
            print(f"Error cargando canción: {e}")

    await msg.edit(content=f"✅ {exitosas}/{total} canciones cargadas desde **{nombre}**.")

    voice_client = ctx.voice_client or await ctx.author.voice.channel.connect()
    if not voice_client.is_playing() and queue:
        await play_next(ctx.guild.id)

@bot.command(name="delpl")
async def eliminar_playlist(ctx, *, nombre: str):
    guild_id = str(ctx.guild.id)
    resultado = playlists_collection.delete_one({"guild_id": guild_id, "name": nombre})

    if resultado.deleted_count == 0:
        return await ctx.send("❌ No se encontró esa playlist para eliminar.")
    await ctx.send(f"🗑️ Playlist **{nombre}** eliminada correctamente.")

@bot.command(name="listpl")
async def listar_playlists(ctx):
    guild_id = str(ctx.guild.id)
    resultados = playlists_collection.find({"guild_id": guild_id})

    nombres = [f"- {doc['name']}" for doc in resultados]
    if not nombres:
        return await ctx.send("📭 No hay playlists guardadas.")

    mensaje = "🎶 **Playlists guardadas:**\n" + "\n".join(nombres)
    await ctx.send(mensaje)


@bot.command(name="editpl")
async def editar_playlist(ctx):
    guild_id = str(ctx.guild.id)

    # Obtener todas las playlists disponibles
    listas = playlists_collection.find({"guild_id": guild_id})
    opciones = [pl["name"] for pl in listas]

    if not opciones:
        return await ctx.send("📭 No hay playlists guardadas para editar.")

    lista_str = "\n".join([f"{i+1}. {n}" for i, n in enumerate(opciones)])
    await ctx.send(f"📚 **Playlists disponibles:**\n{lista_str}\n\nResponde con el número de la playlist que deseas editar:")

    def check(m):
        return m.author == ctx.author and m.channel == ctx.channel and m.content.isdigit()

    try:
        msg = await bot.wait_for("message", timeout=30.0, check=check)
        index = int(msg.content) - 1
        if index < 0 or index >= len(opciones):
            return await ctx.send("❌ Número inválido.")
        nombre = opciones[index]
    except asyncio.TimeoutError:
        return await ctx.send("⌛ Tiempo agotado.")

    playlist = playlists_collection.find_one({"guild_id": guild_id, "name": nombre})
    canciones = playlist.get("songs", [])

    embed = discord.Embed(
        title=f"🛠 Editar Playlist: {nombre}",
        description="Selecciona una opción:",
        color=discord.Color.blurple()
    )
    embed.add_field(name="1️⃣ Ver canciones", value="Lista todas las canciones.", inline=False)
    embed.add_field(name="2️⃣ Eliminar canción", value="Elimina una canción por número.", inline=False)
    embed.add_field(name="3️⃣ Agregar desde la cola", value="Agrega todas las canciones en cola.", inline=False)
    embed.add_field(name="4️⃣ Agregar por URL", value="Agrega una canción por URL.", inline=False)
    embed.add_field(name="5️⃣ Cancelar", value="Cancelar la edición.", inline=False)

    message = await ctx.send(embed=embed)
    botones = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
    for emoji in botones:
        await message.add_reaction(emoji)

    def check_reaction(reaction, user):
        return user == ctx.author and reaction.message.id == message.id and str(reaction.emoji) in botones

    try:
        reaction, _ = await bot.wait_for("reaction_add", timeout=60.0, check=check_reaction)
    except asyncio.TimeoutError:
        return await ctx.send("⌛ Tiempo agotado.")

    emoji = str(reaction.emoji)

    if emoji == "1️⃣":
        if not canciones:
            return await ctx.send("🎵 Esta playlist está vacía.")
        lista = "\n".join([f"{i+1}. {c.get('title', 'Sin título')}" for i, c in enumerate(canciones)])
        return await ctx.send(f"🎼 **Canciones en {nombre}:**\n{lista}")

    elif emoji == "2️⃣":
        if not canciones:
            return await ctx.send("🎵 Esta playlist está vacía.")
        lista = "\n".join([f"{i+1}. {c.get('title', 'Sin título')}" for i, c in enumerate(canciones)])
        await ctx.send(f"🎯 ¿Qué canción deseas eliminar?\n{lista}\nResponde con un número del 1 al {len(canciones)}:")

        def check_msg(m):
            return m.author == ctx.author and m.channel == ctx.channel and m.content.isdigit()

        try:
            msg = await bot.wait_for("message", timeout=30.0, check=check_msg)
            index = int(msg.content) - 1
            if 0 <= index < len(canciones):
                eliminada = canciones.pop(index)
                playlists_collection.update_one(
                    {"guild_id": guild_id, "name": nombre},
                    {"$set": {"songs": canciones}}
                )
                await ctx.send(f"🗑️ Canción **{eliminada.get('title', 'Sin título')}** eliminada.")
            else:
                await ctx.send("⚠️ Número fuera de rango.")
        except asyncio.TimeoutError:
            await ctx.send("⌛ Tiempo agotado.")

    elif emoji == "3️⃣":
        nuevas = [c.copy() for c in music_queue.get_queue(ctx.guild.id)]
        if not nuevas:
            return await ctx.send("❌ No hay canciones en cola.")
        canciones.extend(nuevas)
        playlists_collection.update_one(
            {"guild_id": guild_id, "name": nombre},
            {"$set": {"songs": canciones}}
        )
        await ctx.send(f"➕ Se agregaron {len(nuevas)} canciones desde la cola.")

    elif emoji == "4️⃣":
        await ctx.send("📥 Envía el enlace de la canción que deseas agregar:")

        def check_url(m):
            return m.author == ctx.author and m.channel == ctx.channel

        try:
            msg = await bot.wait_for("message", timeout=45.0, check=check_url)
            url = msg.content

            with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
                info = ydl.extract_info(url, download=False)
                cancion = {
                    "title": info.get("title", "Sin título"),
                    "url": url
                }
            canciones.append(cancion)
            playlists_collection.update_one(
                {"guild_id": guild_id, "name": nombre},
                {"$set": {"songs": canciones}}
            )
            await ctx.send(f"🎵 Canción **{cancion['title']}** agregada.")
        except Exception as e:
            await ctx.send(f"❌ Error al agregar canción: `{e}`")

    elif emoji == "5️⃣":
        await ctx.send("❎ Edición cancelada.")



# --------------------------
# Funciones de Reproducción
# --------------------------

async def play_next(guild_id: int, error=None):
    voice_client = discord.utils.get(bot.voice_clients, guild=bot.get_guild(guild_id))
    
    if error:
        print(f"Error en reproducción: {error}")
        music_queue.set_playing(guild_id, False)
    
    if not voice_client or not voice_client.is_connected():
        music_queue.set_playing(guild_id, False)
        return

    queue = music_queue.get_queue(guild_id)
    
    if not queue:
        music_queue.set_playing(guild_id, False)
        return
    
    next_song = queue.popleft()
    music_queue.current[guild_id] = next_song
    music_queue.set_playing(guild_id, True)
    
    try:
        source = await discord.FFmpegOpusAudio.from_probe(
            next_song['url'],
            **FFMPEG_OPTIONS,
            method='fallback'
        )
        
        voice_client.play(source, after=lambda e: asyncio.run_coroutine_threadsafe(play_next(guild_id, e), bot.loop))
        
        guild = bot.get_guild(guild_id)
        text_channel = next((channel for channel in guild.text_channels if channel.id == next_song.get('request_channel_id')), None)
        
        if text_channel:
            embed = discord.Embed(
                title="🎵 Reproduciendo ahora",
                description=f"[{next_song['title']}]({next_song['youtube_url']})",
                color=discord.Color.blurple()
            )
            embed.set_thumbnail(url=next_song.get('thumbnail', 'https://i.imgur.com/8QZQZ.png'))
            embed.add_field(name="Duración", value=f"{next_song['duration']//60}:{next_song['duration']%60:02}", inline=True)
            embed.add_field(name="Solicitado por", value=next_song.get('requested_by', 'Desconocido'), inline=True)
            
            await text_channel.send(embed=embed)
        
    except Exception as e:
        print(f"Error al reproducir: {traceback.format_exc()}")
        music_queue.set_playing(guild_id, False)
        await asyncio.sleep(2)
        await play_next(guild_id)

# --------------------------
# Comandos de Música (con mensajes mejorados)
# --------------------------

@bot.command(name="play", aliases=["p"])
async def play(ctx, *, query: str):
    """Reproduce música desde YouTube o la añade a la cola"""
    if not ctx.author.voice:
        embed = discord.Embed(
            title="🚨 Error de Comando",
            description="Debes estar en un canal de voz para reproducir música.",
            color=discord.Color.red()
        )
        return await ctx.send(embed=embed)

    try:
        # Mostrar mensaje de "procesando"
        processing_embed = discord.Embed(
            title="🔍 Buscando canción...",
            description="Por favor espera mientras proceso tu solicitud.",
            color=discord.Color.orange()
        )
        processing_msg = await ctx.send(embed=processing_embed)
        
        data = await MusicPlayer.get_audio_source(query)
        if not data:
            error_embed = discord.Embed(
                title="❌ Error en la búsqueda",
                description="No se pudo encontrar el video o canción solicitada.",
                color=discord.Color.red()
            )
            return await processing_msg.edit(embed=error_embed)

        data["requested_by"] = ctx.author.display_name
        data["request_channel_id"] = ctx.channel.id
        
        voice_client = ctx.voice_client or await ctx.author.voice.channel.connect()
        
        queue = music_queue.get_queue(ctx.guild.id)
        queue.append(data)

        # Crear embed de respuesta
        embed = discord.Embed(
            title="🎶 Canción añadida",
            description=f"[{data['title']}]({data['youtube_url']})",
            color=discord.Color.green()
        )
        embed.set_thumbnail(url=data.get('thumbnail', 'https://i.imgur.com/8QZQZ.png'))
        
        if not voice_client.is_playing() and not music_queue.get_playing(ctx.guild.id):
            embed.set_footer(text="Reproduciendo ahora...")
            await processing_msg.edit(embed=embed)
            await play_next(ctx.guild.id)
        else:
            position = len(queue)
            embed.add_field(name="Posición en cola", value=f"#{position}", inline=True)
            embed.set_footer(text="La canción ha sido añadida a la cola de reproducción")
            await processing_msg.edit(embed=embed)

    except Exception as e:
        error_embed = discord.Embed(
            title="❌ Error en reproducción",
            description="Ocurrió un error al procesar tu solicitud.",
            color=discord.Color.red()
        )
        await ctx.send(embed=error_embed)
        print(f"Error en play: {traceback.format_exc()}")

@bot.command(name="skip")
async def skip(ctx):
    """Salta la canción actual"""
    voice_client = ctx.voice_client
    if not voice_client:
        embed = discord.Embed(
            title="🚨 Error de Comando",
            description="No estoy conectado a un canal de voz.",
            color=discord.Color.red()
        )
        return await ctx.send(embed=embed)
    
    queue = music_queue.get_queue(ctx.guild.id)
    if not queue and not music_queue.get_playing(ctx.guild.id):
        embed = discord.Embed(
            title="🚨 Cola Vacía",
            description="No hay música en la cola para saltar.",
            color=discord.Color.red()
        )
        return await ctx.send(embed=embed)
    
    if voice_client.is_playing() or voice_client.is_paused():
        embed = discord.Embed(
            title="⏭️ Saltando canción",
            description="La canción actual ha sido saltada.",
            color=discord.Color.blue()
        )
        await ctx.send(embed=embed)
        voice_client.stop()
    else:
        embed = discord.Embed(
            title="🚨 Error de Reproducción",
            description="No hay música reproduciéndose actualmente.",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)

@bot.command(name="stop")
async def stop(ctx):
    """Detiene la música y limpia la cola"""
    voice_client = ctx.voice_client
    if voice_client:
        music_queue.clear(ctx.guild.id)
        if voice_client.is_playing():
            voice_client.stop()
        await voice_client.disconnect()
        
        embed = discord.Embed(
            title="⏹️ Reproducción detenida",
            description="La música ha sido detenida y el bot se ha desconectado.",
            color=discord.Color.blue()
        )
        await ctx.send(embed=embed)
    else:
        embed = discord.Embed(
            title="🚨 Error de Comando",
            description="No estoy conectado a un canal de voz.",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)


@bot.command(name="queue")
async def mostrar_cola(ctx, pagina: int = 1):
    queue = list(music_queue.get_queue(ctx.guild.id))
    current = music_queue.current.get(ctx.guild.id)

    if not queue and not current:
        return await ctx.send("📭 No hay canciones en la cola.")

    canciones_por_pagina = 10
    total_canciones = len(queue)
    total_paginas = (total_canciones + canciones_por_pagina - 1) // canciones_por_pagina

    if pagina < 1 or pagina > total_paginas:
        return await ctx.send(f"❌ Página inválida. Debe ser entre 1 y {total_paginas}.")

    inicio = (pagina - 1) * canciones_por_pagina
    fin = inicio + canciones_por_pagina

    embed = discord.Embed(title="🎵 Cola de reproducción", color=discord.Color.blurple())

    if current:
        embed.add_field(name="🔊 Sonando ahora", value=f"**{current['title']}**", inline=False)

    if total_canciones > 0:
        canciones_mostradas = queue[inicio:fin]
        descripcion = ""
        for i, song in enumerate(canciones_mostradas, start=inicio + 1):
            descripcion += f"`{i}.` {song['title']}\n"
        embed.add_field(name=f"⏭️ En cola (Página {pagina}/{total_paginas})", value=descripcion, inline=False)

    await ctx.send(embed=embed)

@bot.command(name="pause")
async def pause(ctx):
    """Pausa la reproducción actual"""
    voice = ctx.voice_client
    if voice and voice.is_playing():
        voice.pause()
        embed = discord.Embed(
            title="⏸️ Reproducción pausada",
            description="La música ha sido pausada. Usa `!resume` para continuar.",
            color=discord.Color.blue()
        )
        await ctx.send(embed=embed)
    else:
        embed = discord.Embed(
            title="🚨 Error de Comando",
            description="No hay música reproduciéndose actualmente.",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)

@bot.command(name="resume")
async def resume(ctx):
    """Reanuda la reproducción pausada"""
    voice = ctx.voice_client
    if voice and voice.is_paused():
        voice.resume()
        embed = discord.Embed(
            title="▶️ Reproducción reanudada",
            description="La música ha sido reanudada.",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)
    else:
        embed = discord.Embed(
            title="🚨 Error de Comando",
            description="No hay música pausada actualmente.",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)

@bot.command(name="nowplaying", aliases=["np"])
async def nowplaying(ctx):
    """Muestra la canción actual"""
    if ctx.guild.id in music_queue.current:
        current_song = music_queue.current[ctx.guild.id]
        embed = discord.Embed(
            title="🎵 Reproduciendo ahora",
            description=f"[{current_song['title']}]({current_song['youtube_url']})",
            color=discord.Color.blurple()
        )
        embed.set_thumbnail(url=current_song.get('thumbnail', 'https://i.imgur.com/8QZQZ.png'))
        embed.add_field(name="Duración", value=f"{current_song['duration']//60}:{current_song['duration']%60:02}", inline=True)
        embed.add_field(name="Solicitado por", value=current_song.get('requested_by', 'Desconocido'), inline=True)
        await ctx.send(embed=embed)
    else:
        queue = music_queue.get_queue(ctx.guild.id)
        if queue:
            embed = discord.Embed(
                title="⏸️ Estado de Reproducción",
                description="Hay música en cola pero no se está reproduciendo actualmente.",
                color=discord.Color.orange()
            )
            await ctx.send(embed=embed)
        else:
            embed = discord.Embed(
                title="📭 Sin Reproducción",
                description="No hay música reproduciéndose o en cola.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)


@bot.command(name="shuffle")
async def shuffle(ctx):
    """Mezcla aleatoriamente la cola de reproducción"""
    if not ctx.author.voice:
        embed = discord.Embed(
            title="🚨 Error de Comando",
            description="Debes estar en un canal de voz para usar este comando.",
            color=discord.Color.red()
        )
        return await ctx.send(embed=embed)
    
    queue = music_queue.get_queue(ctx.guild.id)
    
    if len(queue) < 2:
        embed = discord.Embed(
            title="🔀 Shuffle",
            description="Necesitas al menos 2 canciones en cola para mezclar.",
            color=discord.Color.orange()
        )
        return await ctx.send(embed=embed)
    
    # Convertir deque a lista para shuffling
    queue_list = list(queue)
    # Mezclar la lista
    random.shuffle(queue_list)
    # Limpiar y reemplazar la cola original
    queue.clear()
    queue.extend(queue_list)
    
    embed = discord.Embed(
        title="🔀 Cola mezclada",
        description=f"Se han reordenado aleatoriamente {len(queue_list)} canciones.",
        color=discord.Color.green()
    )
    await ctx.send(embed=embed)



# --------------------------
# Comandos de Información
# --------------------------

@bot.command(name="changelog")
async def mostrar_changelog(ctx):
    embed = discord.Embed(
    title="📜 Registro de Cambios Recientes",
    description="Mejoras aplicadas recientemente al bot musical:",
    color=discord.Color.green()
    )

    if ctx.guild.icon:
        embed.set_thumbnail(url=ctx.guild.icon.url)

    if ctx.author.avatar:
        embed.set_footer(text="Actualizado por el equipo del bot 🎧", icon_url=ctx.author.avatar.url)
    else:
        embed.set_footer(text="Actualizado por el equipo del bot 🎧")

    # SISTEMA DE PLAYLISTS
    embed.add_field(
        name="🧠 Sistema de playlists mejorado",
        value=(
            "- Migración de JSON a SQLite\n"
            "- Soporte para nombres con espacios\n"
            "- Playlists por servidor\n"
            "- Guardar, cargar y renombrar sin pausar la música"
        ),
        inline=False
    )

    # EDITOR INTERACTIVO
    embed.add_field(
        name="🛠️ Nuevo comando `!editpl`",
        value=(
            "- Menú interactivo con reacciones\n"
            "- Ver canciones con numeración\n"
            "- Eliminar por número\n"
            "- Agregar desde cola o URL"
        ),
        inline=False
    )

    # MEJORAS VISUALES
    embed.add_field(
        name="🎨 Estética y comandos agrupados",
        value=(
            "- `!comandos` rediseñado con secciones:\n"
            "  🎵 Reproducción / 📁 Playlists / ⚙️ Utilidades\n"
            "- Mejor uso de colores, emojis y descripciones"
        ),
        inline=False
    )

    # NUEVAS FUNCIONES
    embed.add_field(
        name="🎵 Nuevos comandos y mejoras",
        value=(
            "- `!shuffle`: mezcla la cola\n"
            "- `!nowplaying` / `!np`: muestra canción actual\n"
            "- `!changelog`: muestra este registro"
        ),
        inline=False
    )

    await ctx.send(embed=embed)


@bot.command(name="comandos")
async def mostrar_comandos(ctx):
    embed = discord.Embed(
        title="🎶 Panel de Comandos del Bot Musical",
        description="Aquí tienes una lista completa de los comandos disponibles, organizados por categoría:",
        color=discord.Color.blurple()
    )
    embed.set_thumbnail(url=ctx.guild.icon.url if ctx.guild.icon else discord.Embed.Empty)
    embed.set_footer(text="Disfruta la música 🎧", icon_url=ctx.author.avatar.url if ctx.author.avatar else None)

    categorias = {
        "🎵 Reproducción": [
            ("!play <url o búsqueda>", "Reproduce una canción desde YouTube o Spotify."),
            ("!pause", "Pausa la canción actual."),
            ("!resume", "Reanuda la canción pausada."),
            ("!skip", "Salta a la siguiente canción en la cola."),
            ("!stop", "Detiene la reproducción y sale del canal de voz."),
            ("!volume <1-100>", "Ajusta el volumen del bot."),
            ("!queue", "Muestra la cola de reproducción actual."),
            ("!shuffle", "Mezcla aleatoriamente el orden de las canciones en la cola."),
            ("!nowplaying / !np", "Muestra información de la canción que se está reproduciendo actualmente."),
        ],
        "📁 Playlists": [
            ("!savepl <nombre>", "Guarda la canción actual y la cola en una playlist."),
            ("!loadpl <nombre>", "Carga una playlist guardada."),
            ("!listpl", "Lista todas las playlists guardadas."),
            ("!renamepl nombre_actual | nuevo_nombre", "Renombra una playlist."),
            ("!delpl <nombre>", "Elimina una playlist."),
            ("!editpl", "Editar una playlist con menú interactivo.")
        ],
        "⚙️ Utilidades": [
            ("!comandos", "Muestra este mensaje."),
            ("!changelog", "Muestra los últimos cambios realizados en el bot.")
        ]
    }

    for categoria, comandos in categorias.items():
        embed.add_field(name=categoria, value="​", inline=False)
        for i, (cmd, desc) in enumerate(comandos, start=1):
            embed.add_field(
                name=f"`{cmd}`",
                value=f"{desc}",
                inline=False
            )

    await ctx.send(embed=embed)




# --------------------------
# Eventos
# --------------------------

@bot.event
async def on_voice_state_update(member, before, after):
    if member != bot.user:
        return
    
    if before.channel and not after.channel:
        music_queue.clear(before.channel.guild.id)

@bot.event
async def on_ready():
    print(f"✅ Bot listo como {bot.user}")
    await bot.change_presence(activity=discord.Activity(
    type=discord.ActivityType.playing,
    name="!comandos"
))
# --------------------------
# Ejecución del Bot
# --------------------------

bot.run(os.getenv("TOKEN"))
