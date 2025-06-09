import discord
from discord.ext import commands
import os
from dotenv import load_dotenv
import yt_dlp
from collections import deque
import asyncio
import traceback

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
    'options': '-vn -c:a libopus -b:a 128k -ar 48000 -ac 2 -af "dynaudnorm=g=12:f=150,acompressor=threshold=0.5:ratio=2:attack=20:release=250"',
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
        
        # Crear embed para mostrar la canción actual
        embed = discord.Embed(
            title="🎵 Reproduciendo ahora",
            description=f"[{next_song['title']}]({next_song['url']})",
            color=discord.Color.blurple()
        )
        embed.set_thumbnail(url=next_song.get('thumbnail', 'https://i.imgur.com/8QZQZ.png'))
        embed.add_field(name="Duración", value=f"{next_song['duration']//60}:{next_song['duration']%60:02}", inline=True)
        embed.add_field(name="Solicitado por", value=next_song.get('requested_by', 'Desconocido'), inline=True)
        
        channel = voice_client.channel
        await channel.send(embed=embed)
        
        await bot.change_presence(activity=discord.Activity(
            type=discord.ActivityType.listening,
            name=next_song['title'][:50]
        ))
        
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

@bot.command(name="queue", aliases=["q"])
async def queue(ctx):
    """Muestra la cola de reproducción"""
    queue_list = []
    
    if ctx.guild.id in music_queue.current:
        current_song = music_queue.current[ctx.guild.id]
        queue_list.append(f"**🔊 Reproduciendo ahora:**\n[1. {current_song['title']}]({current_song['url']}) - 🎵 Solicitado por {current_song.get('requested_by', 'Desconocido')}")
    
    queue = music_queue.get_queue(ctx.guild.id)
    if queue:
        queue_list.append("\n**📜 En cola:**")
        start = 2 if ctx.guild.id in music_queue.current else 1
        for i, song in enumerate(list(queue)[:10], start=start):
            queue_list.append(f"{i}. [{song['title']}]({song['url']}) - 🎵 Solicitado por {song.get('requested_by', 'Desconocido')}")
    
    if queue_list:
        embed = discord.Embed(
            title="🎶 Cola de Reproducción",
            description="\n".join(queue_list),
            color=discord.Color.blurple()
        )
        await ctx.send(embed=embed)
    else:
        embed = discord.Embed(
            title="📭 Cola Vacía",
            description="No hay música en la cola de reproducción.",
            color=discord.Color.orange()
        )
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




# --------------------------
# Comandos de Información
# --------------------------

@bot.command(name="changelog")
async def changelog(ctx):
    """Muestra los últimos cambios en el bot"""
    embed = discord.Embed(
        title="🎉 Changelog - Actualización Estética del Bot de Música 🎶",
        description="Aquí están los últimos cambios realizados en el bot:",
        color=discord.Color.gold()
    )
    
    embed.add_field(
        name="📅 Fecha de la Actualización",
        value=ctx.message.created_at.strftime("%Y-%m-%d"),
        inline=False
    )
    
    embed.add_field(
        name="✨ Versión",
        value='2.0 - "Mensajes con Estilo"',
        inline=False
    )
    
    embed.add_field(
        name="🌟 Novedades Principales",
        value="""
**🎨 Interfaz Mejorada**
🔹 Todos los mensajes ahora usan Embeds de Discord con colores temáticos
🔹 Miniaturas de canciones integradas
🔹 Enlaces clickeables a los videos de YouTube

**📢 Mensajes Más Claros y Detallados**
🔸 Procesamiento en tiempo real
🔸 Información enriquecida (duración, solicitante, estado de cola)
🔸 Errores más descriptivos
""",
        inline=False
    )
    
    embed.add_field(
        name="🛠️ Cambios Técnicos",
        value="""
🔧 Optimización de código
🔧 Mejoras en la respuesta de voz
""",
        inline=False
    )
    
    embed.add_field(
        name="📜 Lista de Comandos Actualizados",
        value="""
`!play` - Ahora muestra miniatura, duración y posición en cola
`!skip` - Mensaje de confirmación con estilo
`!queue` - Lista formateada con enlaces y detalles
`!nowplaying` - Muestra portada del video y más metadata
""",
        inline=False
    )
    
    embed.add_field(
        name="🐛 Correcciones de Bugs",
        value="""
- Arreglado problema con errores poco claros
- Mejor manejo de desconexiones inesperadas
""",
        inline=False
    )
    
    embed.add_field(
        name="🎁 Agradecimientos",
        value="¡Gracias por usar el bot! Esperamos que esta actualización haga la experiencia más agradable.",
        inline=False
    )
    
    embed.set_footer(text="¡Disfruta de la música con estilo! 🎧✨")
    
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
        name="!help"
    ))

# --------------------------
# Ejecución del Bot
# --------------------------

bot.run(os.getenv("TOKEN"))
