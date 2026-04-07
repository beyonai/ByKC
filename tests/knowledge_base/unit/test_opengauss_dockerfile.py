from pathlib import Path

DOCKERFILE = Path("docker/opengauss/custom/Dockerfile")


def test_opengauss_dockerfile_uses_matching_opengauss_source_archive():
    """The custom image should build extensions from openGauss source, not vanilla PostgreSQL."""
    content = DOCKERFILE.read_text(encoding="utf-8")

    assert "openGauss-server" in content
    assert "postgresql-" not in content


def test_opengauss_dockerfile_syncs_missing_headers_from_source_into_server_includes():
    """The runtime image misses a subset of server headers, so the build should fill gaps from src/include."""
    content = DOCKERFILE.read_text(encoding="utf-8")

    assert "OPENGAUSS_SERVER_INCLUDE_DIR" in content
    assert "/src/include/" in content


def test_opengauss_dockerfile_overlays_server_headers_from_source():
    """The build should overlay source headers onto the installed include tree to keep them version-consistent."""
    content = DOCKERFILE.read_text(encoding="utf-8")

    assert "cp -r" in content


def test_opengauss_dockerfile_provides_openssl_headers_for_extension_build():
    """openGauss source headers pull in cipher.h, so the builder image must provide OpenSSL headers."""
    content = DOCKERFILE.read_text(encoding="utf-8")

    assert "openssl-devel" in content
    assert "openssl/comm/include" in content or "/usr/include" in content


def test_opengauss_dockerfile_provides_libaio_headers_for_extension_build():
    """openGauss source headers also pull in ss_aio.h, which requires libaio.h."""
    content = DOCKERFILE.read_text(encoding="utf-8")

    assert "libaio-devel" in content


def test_opengauss_dockerfile_downloads_source_in_a_separate_stage():
    """Source download should stay cached even if we tweak builder dependencies."""
    content = DOCKERFILE.read_text(encoding="utf-8")

    assert "AS source" in content
    assert "COPY --from=source" in content


def test_opengauss_dockerfile_includes_obs_header_directory():
    """The base image ships eSDKOBS.h under access/obs, which needs an explicit include path."""
    content = DOCKERFILE.read_text(encoding="utf-8")

    assert "OPENGAUSS_SERVER_INCLUDE_DIR" in content
    assert "access/obs" in content


def test_opengauss_dockerfile_enables_pgxc_definitions_for_extension_builds():
    """openGauss server headers gate several required types behind PGXC."""
    content = DOCKERFILE.read_text(encoding="utf-8")

    assert "-DPGXC" in content


def test_opengauss_dockerfile_patches_ltree_selectivity_signature():
    """The downloaded ltree source needs a compatibility patch for the current mcv_selectivity signature."""
    content = DOCKERFILE.read_text(encoding="utf-8")

    assert "mcv_selectivity" in content
    assert "InvalidOid" in content


def test_opengauss_dockerfile_patches_ltree_pic_flags():
    """AArch64 shared linking needs ltree to drop -fPIE and build with -fPIC."""
    content = DOCKERFILE.read_text(encoding="utf-8")

    assert "exclude_option = -fPIE" in content
    assert "override CXXFLAGS" in content
