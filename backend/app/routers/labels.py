import io
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import SKU

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/labels", tags=["labels"], dependencies=[Depends(get_current_user)]
)


def _get_sku(db: Session, sku_id: int) -> SKU:
    sku = db.get(SKU, sku_id)
    if not sku:
        raise HTTPException(404, "SKU not found")
    return sku


@router.get("/{sku_id}/barcode.png")
def barcode_png(
    sku_id: int,
    db: Session = Depends(get_db),
    barcode_type: str = Query("code128", pattern="^(code128|ean13)$"),
):
    """Generate a Code128 barcode as PNG image."""
    import barcode
    from barcode.writer import ImageWriter

    sku = _get_sku(db, sku_id)

    writer = ImageWriter()
    writer.set_options(
        {
            "module_width": 0.4,
            "module_height": 15.0,
            "font_size": 10,
            "text_distance": 5.0,
            "quiet_zone": 6.5,
        }
    )

    code = barcode.get(barcode_type, sku.sku_code, writer=writer)
    buf = io.BytesIO()
    code.write(buf)
    buf.seek(0)

    return Response(
        content=buf.getvalue(),
        media_type="image/png",
        headers={"Content-Disposition": f'inline; filename="{sku.sku_code}.png"'},
    )


@router.get("/{sku_id}/label.zpl")
def label_zpl(
    sku_id: int,
    db: Session = Depends(get_db),
):
    """Generate ZPL label for a Zebra printer.

    Includes: SKU code (barcode) and product name.
    """
    sku = _get_sku(db, sku_id)

    # Standard 4x2 inch label at 203 DPI
    zpl = f"""^XA
^CF0,30
^FO50,30^FD{sku.name}^FS
^CF0,20
^FO50,70^FDSKU: {sku.sku_code}^FS
^BY2,2,80
^FO50,120^BC,,Y,N^FD{sku.sku_code}^FS
^XZ"""

    return Response(
        content=zpl,
        media_type="text/plain",
        headers={
            "Content-Disposition": f'attachment; filename="{sku.sku_code}.zpl"'
        },
    )


@router.get("/{sku_id}/label.pdf")
def label_pdf(
    sku_id: int,
    db: Session = Depends(get_db),
):
    """Generate a printable PDF label with barcode.

    Fallback for when no Zebra printer is available — print from browser.
    """
    import barcode
    from barcode.writer import ImageWriter

    sku = _get_sku(db, sku_id)

    # Generate barcode image
    writer = ImageWriter()
    writer.set_options(
        {
            "module_width": 0.5,
            "module_height": 18.0,
            "font_size": 14,
            "text_distance": 5.0,
            "quiet_zone": 6.5,
        }
    )
    code = barcode.get("code128", sku.sku_code, writer=writer)
    barcode_buf = io.BytesIO()
    code.write(barcode_buf)
    barcode_buf.seek(0)

    import base64

    barcode_buf.seek(0)
    barcode_b64 = base64.b64encode(barcode_buf.read()).decode()

    html = f"""<!DOCTYPE html>
<html>
<head>
<style>
  @page {{ size: 100mm 60mm; margin: 5mm; }}
  body {{ font-family: Arial, sans-serif; margin: 0; padding: 5mm; }}
  .name {{ font-size: 16pt; font-weight: bold; margin-bottom: 4mm; }}
  .info {{ font-size: 10pt; color: #333; margin-bottom: 2mm; }}
  .barcode {{ margin-top: 4mm; text-align: center; }}
  .barcode img {{ max-width: 80mm; height: auto; }}
</style>
</head>
<body>
  <div class="name">{sku.name}</div>
  <div class="info">SKU: {sku.sku_code}</div>
  <div class="barcode">
    <img src="data:image/png;base64,{barcode_b64}" />
  </div>
</body>
</html>"""

    return Response(
        content=html,
        media_type="text/html",
        headers={"Content-Disposition": f'inline; filename="{sku.sku_code}-label.html"'},
    )
