"""Strategy templates: the catalogue the front end renders forms from, and template -> DSL spec building.

Templates only *produce* DSL documents. The spec returned here is what gets saved (POST /strategies) or tested
(POST /backtests with ``spec``); the engine executes nothing template-specific.
"""

from __future__ import annotations

from typing import Any

from algora_engine.dsl import Instrument
from algora_engine.templates import TemplateUnavailable, build_template, catalogue, params_model
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.encoders import jsonable_encoder
from pydantic import ValidationError

from ..ratelimit import per_ip
from ..schemas import TemplateSpecIn, TemplateSpecOut

router = APIRouter(prefix="/templates", tags=["templates"])


def _errors(e: ValidationError) -> Any:
    return jsonable_encoder(e.errors(include_url=False, include_context=False))


@router.get("", dependencies=[Depends(per_ip("meta", 60, 60))])
def list_templates() -> dict:
    """Every template: id, name, description, whether the engine can run it, parameter JSON schema and defaults.

    Parameters left empty for times (``start``, ``square_off``) take the exchange default listed in
    ``exchange_defaults``.
    """
    return {"templates": catalogue()}


@router.post("/{template_id}/spec", dependencies=[Depends(per_ip("template-spec", 120, 60))])
def template_spec(template_id: str, body: TemplateSpecIn) -> TemplateSpecOut:
    """Build the DSL strategy for a template from form values. Nothing is saved."""
    try:
        model = params_model(template_id)
    except TemplateUnavailable as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from None
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Unknown template") from None
    try:
        instrument = Instrument.model_validate(body.instrument)
        spec = build_template(template_id, name=body.name, instrument=instrument, params=body.params)
    except ValidationError as e:  # parameters, instrument, or the resulting strategy (e.g. range after square-off)
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=_errors(e)) from None
    return TemplateSpecOut(
        template=template_id,
        spec=spec.model_dump(mode="json"),
        params_used=spec.template_params,
        defaults_used=sorted(k for k in model.model_fields if k not in body.params),
    )
