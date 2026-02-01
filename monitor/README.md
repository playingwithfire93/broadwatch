Monitor Flask para BroadWatch

Archivos:
- `app.py`: script principal que arranca un servidor Flask y ejecuta el monitor en segundo plano.
- `requirements.txt`: dependencias necesarias.

Instrucciones rápidas (Windows / PowerShell):

1) Crear entorno virtual e instalar dependencias

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r monitor/requirements.txt
```

2) Definir variables sensibles en el entorno (recomendado):

```powershell
$env:BROADWATCH_TELEGRAM_TOKEN = "tu_token"
$env:BROADWATCH_TELEGRAM_CHAT_ID = "tu_chat_id"
$env:BROADWATCH_TWILIO_SID = "tu_sid"
$env:BROADWATCH_TWILIO_TOKEN = "tu_token"
```

3) Ejecutar el monitor

```powershell
python monitor/app.py
```

Notas:
- Se recomienda mover credenciales a variables de entorno antes de desplegar.
- Para producción, ejecutar el monitor como servicio o usar un process manager (Supervisor, NSSM, systemd, etc.).
- Si quieres, adapto el monitor para exponer endpoints API para listar URLs, activar/desactivar monitoreo y ver logs.
