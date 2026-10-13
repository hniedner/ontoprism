"""Shared pinned ROBOT JVM invocation with bounded resource settings."""

import shutil
import subprocess
from typing import Annotated

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from ontolib.common.boundary_models import StrictFrozenBoundaryModel
from ontolib.core.data_build_tools import configured_robot_installation


class RobotConfig(StrictFrozenBoundaryModel):
    heap_gib: Annotated[int, Field(gt=0)]
    max_element_depth: Annotated[int, Field(gt=0)]
    timeout_seconds: Annotated[int, Field(gt=0)]


class RobotSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="RELEASE_DELTA_", env_file=".env", extra="ignore", strict=True
    )
    robot_heap_gib: Annotated[int, Field(gt=0)]
    robot_max_element_depth: Annotated[int, Field(gt=0)]
    robot_timeout_seconds: Annotated[int, Field(gt=0)]

    def robot_config(self):
        return RobotConfig(
            **{k: getattr(self, "robot_" + k) for k in RobotConfig.model_fields}
        )


def run_robot(
    arguments: list[str], config: RobotConfig
) -> subprocess.CompletedProcess[str]:
    install, _ = configured_robot_installation()
    java = shutil.which("java")
    if java is None:
        raise RuntimeError("Java is required for ROBOT")
    return subprocess.run(  # noqa: S603 -- pinned tool and validated bounded JVM settings
        [
            java,
            f"-Xmx{config.heap_gib}g",
            f"-Djdk.xml.maxElementDepth={config.max_element_depth}",
            "-jar",
            str(install / "robot.jar"),
            *arguments,
        ],
        capture_output=True,
        text=True,
        timeout=config.timeout_seconds,
        check=False,
    )
