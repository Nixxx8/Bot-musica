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

# --------------------------
# Configuración Inicial
# --------------------------

load_dotenv()

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Configuración de audio
FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -probesize 32M -analyzeduration 32M',
    'options': '-vn -c:a libopus -b:a 192k -ar 48000 -ac 2 -af "loudnorm=I=-16:TP=-1.5:LRA=11,acompressor=threshold=-20dB:ratio=4:attack=50:release=200"',
    'executable': 'ffmpeg',
}

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
            with yt_dlp.YoutubeDL(cls.YDL_OPTIONS) as ydl:
                if not query.startswith(('http://', 'https://')):
                    query = f"ytsearch:{query}"
                
                info = await bot.loop.run_in_executor(None, lambda: ydl.extract_info(query, download=False))
                
                if 'entries' in info:
                    info = info['entries'][0]
                
                return {
                    'url': info['url'],
                    'title': info.get('title', 'Audio desconocido'),
                    'duration': info.get('duration', 0),
                    'thumbnail': info.get('thumbnail', 'https://i.imgur.com/8QZQZ.png'),
                    'requested_by': 'Solicitado'
                }
        except Exception:
            print(f"Error al obtener audio: {traceback.format_exc()}")
            return None




PLAYLISTS_FILE = "playlists.json"

try:
    with open(PLAYLISTS_FILE, "r", encoding="utf-8") as f:
        playlists = json.load(f)
except FileNotFoundError:
    playlists = {}

@bot.command(name="savepl")
async def save_playlist(ctx, *, nombre: str):
    guild_id = str(ctx.guild.id)

    # Clonar objetos para evitar conflictos con el reproductor
    queue = [c.copy() for c in music_queue.get_queue(ctx.guild.id)]
    current = music_queue.current.get(ctx.guild.id)
    canciones = [current.copy()] if current else []
    canciones.extend(queue)

    if not canciones:
        return await ctx.send("❌ No hay música en reproducción ni en cola para guardar.")

    if guild_id not in playlists:
        playlists[guild_id] = {}
    playlists[guild_id][nombre] = canciones

    try:
        # Guardar en archivo sin afectar reproducción
        with open(PLAYLISTS_FILE, "w", encoding="utf-8") as f:
            json.dump(playlists, f, ensure_ascii=False, indent=4)

        await ctx.send(f"✅ Playlist **{nombre}** guardada con {len(canciones)} canciones.")

    except Exception as e:
        return await ctx.send(f"❌ Error al guardar la playlist: `{e}`")

    except Exception as e:
        return await ctx.send(f"❌ Error al guardar la playlist: `{e}`")


@bot.command(name="loadpl")
async def load_playlist(ctx, *, nombre: str):
    guild_id = str(ctx.guild.id)
    if guild_id not in playlists or nombre not in playlists[guild_id]:
        return await ctx.send("❌ No se encontró esa playlist.")

    lista = playlists[guild_id][nombre]
    if not isinstance(lista, list):
        return await ctx.send("❌ La playlist está corrupta o vacía.")

    queue = music_queue.get_queue(ctx.guild.id)
    queue.extend([c.copy() for c in lista])

    await ctx.send(f"📂 Playlist **{nombre}** cargada con {len(lista)} canciones.")

    voice_client = ctx.voice_client or await ctx.author.voice.channel.connect()
    queue = music_queue.get_queue(ctx.guild.id)

    # Solo iniciar reproducción si no está sonando nada y hay canciones en cola
    if not voice_client.is_playing() and not music_queue.get_playing(ctx.guild.id) and queue:
        await play_next(ctx.guild.id)


@bot.command(name="listpl")
async def list_playlists(ctx):
    guild_id = str(ctx.guild.id)
    if guild_id not in playlists or not playlists[guild_id]:
        return await ctx.send("📭 No hay playlists guardadas en este servidor.")

    nombres = list(playlists[guild_id].keys())
    embed = discord.Embed(
        title="📂 Playlists disponibles",
        description="\n".join(f"• {n}" for n in nombres),
        color=discord.Color.blue()
    )
    await ctx.send(embed=embed)

@bot.command(name="delpl")
async def delete_playlist(ctx, nombre: str):
    guild_id = str(ctx.guild.id)
    if guild_id in playlists and nombre in playlists[guild_id]:
        del playlists[guild_id][nombre]
        with open(PLAYLISTS_FILE, "w", encoding="utf-8") as f:
            json.dump(playlists, f, ensure_ascii=False, indent=4)
        await ctx.send(f"🗑️ Playlist **{nombre}** eliminada.")
    else:
        await ctx.send("❌ Esa playlist no existe.")

@bot.command(name="renamepl")
async def rename_playlist(ctx, nombre_actual: str, nuevo_nombre: str):
    guild_id = str(ctx.guild.id)
    if guild_id not in playlists or nombre_actual not in playlists[guild_id]:
        return await ctx.send("❌ No se encontró esa playlist para renombrar.")

    if nuevo_nombre in playlists[guild_id]:
        return await ctx.send("⚠️ Ya existe una playlist con ese nombre.")

    try:
        playlists[guild_id][nuevo_nombre] = playlists[guild_id].pop(nombre_actual)

        with open(PLAYLISTS_FILE, "w", encoding="utf-8") as f:
            json.dump(playlists, f, ensure_ascii=False, indent=4)

        embed = discord.Embed(
            title="✏️ Playlist renombrada",
            description=f"**{nombre_actual}** fue renombrada exitosamente a **{nuevo_nombre}**.",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)

    except Exception as e:
        error_embed = discord.Embed(
            title="❌ Error al renombrar",
            description=f"Ocurrió un error al renombrar la playlist: `{e}`",
            color=discord.Color.red()
        )
        await ctx.send(embed=error_embed)

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
        
        # Obtener el canal de texto donde se ejecutó el comando
        guild = bot.get_guild(guild_id)
        text_channel = next((channel for channel in guild.text_channels if channel.id == next_song.get('request_channel_id')), None)
        
        if text_channel:
            # Crear embed para mostrar la canción actual
            embed = discord.Embed(
                title="🎵 Reproduciendo ahora",
                description=f"[{next_song['title']}]({next_song['url']})",
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
            description=f"[{data['title']}]({data['url']})",
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
            description=f"[{current_song['title']}]({current_song['url']})",
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
async def changelog(ctx):
    """Muestra los últimos cambios en el bot"""
    embed = discord.Embed(
        title="🎉 CHANGELOG v3.0 - EXPERIENCIA COMPLETA",
        description="Aquí están las revolucionarias mejoras de esta versión:",
        color=discord.Color.gold()
    )
    
    embed.add_field(
        name="📅 Fecha de Lanzamiento",
        value=ctx.message.created_at.strftime("%Y-%m-%d"),
        inline=False
    )
    
    embed.add_field(
        name="🎧 AUDIO PROFESIONAL",
        value="""
**🔊 Calidad de sonido mejorada**
- Bitrate aumentado a 192k (calidad de streaming)
- Normalización inteligente: `loudnorm=I=-16:TP=-1.5:LRA=11`
- Compresor optimizado:
  - Threshold: `-20dB` | Ratio: `4:1`
  - Ataque/liberación suavizados: `attack=50`, `release=200`

**🐛 Errores corregidos:**
- Distorsión en frecuencias altas
- Volumen inconsistente entre pistas
""",
        inline=False
    )
    
    embed.add_field(
        name="🔄 FLUJO INTELIGENTE",
        value="""
**📌 Mensajes contextuales**
- Notificaciones siempre en el canal origen
- Sistema mejorado de rastreo de canales

**🔀 Nuevo comando: `!shuffle`**
- Mezcla profesional con algoritmo Fisher-Yates
- Requiere 2+ canciones en cola
- Confirmación con embed visual

**📜 `!queue` rediseñado**
- Todo en un solo embed sin paginación
- Formato mejorado con duración y solicitante
- Límite inteligente de 20 canciones visibles
""",
        inline=False
    )
    
    embed.add_field(
        name="⚙️ OPTIMIZACIONES",
        value="""
**🛠️ Bajo el capó:**
- Código más eficiente en gestión de colas
- Sistema de errores mejorado
- Mensajes más descriptivos

**📌 Estado permanente:**
- 🎧 Escuchando "Tus favoritas"
""",
        inline=False
    )
    
    embed.add_field(
        name="📌 EJEMPLOS PRÁCTICOS",
        value="""
```bash
!play Bohemian Rhapsody
!shuffle
!queue
```""",
        inline=False
    )
    
    embed.add_field(
        name="🎁 AGRADECIMIENTOS",
        value="¡Gracias por hacer crecer esta comunidad musical! 🎶✨",
        inline=False
    )
    
    embed.set_footer(text="Bot de Música v3.0 | ¡Más que un bot, una experiencia!")
    
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
    type=discord.ActivityType.listening,
    name="Tus favoritas"
))
# --------------------------
# Ejecución del Bot
# --------------------------

bot.run(os.getenv("TOKEN"))
