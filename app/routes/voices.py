from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from app.models.schemas import VoiceListResponse, VoiceInfo, TTSPreviewRequest
from app.services import elevenlabs

router = APIRouter(prefix="/api", tags=["voices"])


@router.get("/voices", response_model=VoiceListResponse)
async def list_voices():
    """List all available ElevenLabs voices."""
    try:
        voices = await elevenlabs.list_voices()
        return VoiceListResponse(
            voices=[VoiceInfo(**v) for v in voices]
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch voices: {e}")


@router.post("/tts/preview")
async def preview_tts(request: TTSPreviewRequest):
    """Preview text-to-speech without full video generation."""
    try:
        audio_bytes = await elevenlabs.text_to_speech(
            text=request.text,
            voice_id=request.voice_id,
            model_id=request.model_id or "eleven_multilingual_v2",
        )
        return Response(
            content=audio_bytes,
            media_type="audio/mpeg",
            headers={"Content-Disposition": "attachment; filename=preview.mp3"},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"TTS failed: {e}")
