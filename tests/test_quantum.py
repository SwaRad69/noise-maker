from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest

from camera_noise.quantum import (
    GeneratedCircuit,
    NoiseSource,
    _bits_per_circuit,
    generate_circuits,
    run_quantum_pipeline,
    simulate_circuits,
)


# ---------------------------------------------------------------------------
# NoiseSource tests
# ---------------------------------------------------------------------------


def test_noise_source_rejects_empty_array() -> None:
    with pytest.raises(ValueError, match="at least one bit"):
        NoiseSource(np.empty(0, dtype=np.uint8))


def test_noise_source_tracks_consumption() -> None:
    bits = np.array([1, 0, 1, 1, 0, 0, 1, 0], dtype=np.uint8)
    source = NoiseSource(bits)
    assert source.total == 8
    assert source.remaining == 8
    assert source.consumed == 0

    taken = source.take_bits(3)
    np.testing.assert_array_equal(taken, [1, 0, 1])
    assert source.consumed == 3
    assert source.remaining == 5


def test_noise_source_take_int_binary_conversion() -> None:
    # bits: 1, 0, 1 -> binary 101 -> 5
    source = NoiseSource(np.array([1, 0, 1, 0, 0], dtype=np.uint8))
    assert source.take_int(3) == 5
    assert source.consumed == 3


def test_noise_source_take_float_maps_to_range() -> None:
    # All ones with 3 bits -> int 7 out of max 7 -> maps to 1.0 in [0, 1]
    source = NoiseSource(np.array([1, 1, 1, 0, 0, 0], dtype=np.uint8))
    value = source.take_float(3, 0.0, 1.0)
    assert abs(value - 1.0) < 1e-10

    # All zeros -> 0.0
    value = source.take_float(3, 0.0, 1.0)
    assert abs(value - 0.0) < 1e-10


def test_noise_source_take_angle_returns_valid_range() -> None:
    rng = np.random.default_rng(42)
    bits = rng.integers(0, 2, size=100, dtype=np.uint8)
    source = NoiseSource(bits)

    for _ in range(9):
        angle = source.take_angle(10)
        assert 0.0 <= angle <= 2 * math.pi


def test_noise_source_exhaustion_raises() -> None:
    source = NoiseSource(np.array([1, 0], dtype=np.uint8))
    source.take_bits(2)

    with pytest.raises(ValueError, match="exhausted"):
        source.take_bits(1)


def test_noise_source_take_bits_zero_returns_empty() -> None:
    source = NoiseSource(np.array([1], dtype=np.uint8))
    result = source.take_bits(0)
    assert len(result) == 0
    assert source.consumed == 0


def test_noise_source_from_symbol_file(tmp_path: Path) -> None:
    bits = np.array([0, 1, 1, 0, 1], dtype=np.uint8)
    path = tmp_path / "test.symbols.bin"
    path.write_bytes(bits.tobytes())

    source = NoiseSource.from_symbol_file(path)
    assert source.total == 5
    np.testing.assert_array_equal(source.take_bits(5), bits)


def test_noise_source_from_packed_file(tmp_path: Path) -> None:
    bits = np.array([1, 0, 1, 1, 0, 0, 1, 0], dtype=np.uint8)
    packed = np.packbits(bits, bitorder="big")
    path = tmp_path / "test.bin"
    path.write_bytes(packed.tobytes())

    source = NoiseSource.from_packed_file(path, meaningful_bits=8)
    assert source.total == 8
    np.testing.assert_array_equal(source.take_bits(8), bits)


# ---------------------------------------------------------------------------
# NoiseSource.from_entropy_report tests
# ---------------------------------------------------------------------------


def _fake_entropy_output(path: Path, n_bits: int = 10000) -> str:
    """Create a minimal fake entropy output directory for testing."""
    path.mkdir(parents=True, exist_ok=True)
    method = "temporal_difference_sign"

    # Create bitstream files
    bitstreams = path / "bitstreams"
    bitstreams.mkdir()

    rng = np.random.default_rng(123)
    bits = rng.integers(0, 2, size=n_bits, dtype=np.uint8)
    symbol_path = bitstreams / f"{method}.symbols.bin"
    symbol_path.write_bytes(bits.tobytes())

    packed_count = n_bits // 8 * 8
    packed = np.packbits(bits[:packed_count], bitorder="big")
    packed_path = bitstreams / f"{method}.bin"
    packed_path.write_bytes(packed.tobytes())

    # Create entropy report
    report = {
        "schema_version": 1,
        "status": "complete",
        "bitstream_comparison": {
            "recommended_diagnostic_candidate": method,
        },
        "bitstream_exports": {
            method: {
                "packed_binary": f"bitstreams/{method}.bin",
                "one_bit_per_byte_binary": f"bitstreams/{method}.symbols.bin",
                "meaningful_bits": n_bits,
                "packed_bits": packed_count,
            },
        },
    }
    (path / "entropy_report.json").write_text(
        json.dumps(report), encoding="utf-8"
    )
    return method


def test_noise_source_from_entropy_report(tmp_path: Path) -> None:
    entropy_dir = tmp_path / "entropy"
    method = _fake_entropy_output(entropy_dir)

    source = NoiseSource.from_entropy_report(entropy_dir)
    assert source.total == 10000

    source2 = NoiseSource.from_entropy_report(entropy_dir, method=method)
    assert source2.total == 10000


def test_noise_source_from_entropy_report_missing_method(tmp_path: Path) -> None:
    entropy_dir = tmp_path / "entropy"
    _fake_entropy_output(entropy_dir)

    with pytest.raises(ValueError, match="not found in bitstream exports"):
        NoiseSource.from_entropy_report(entropy_dir, method="nonexistent_method")


def test_noise_source_from_entropy_report_missing_report(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="No entropy report"):
        NoiseSource.from_entropy_report(tmp_path / "nonexistent")


# ---------------------------------------------------------------------------
# Circuit generation tests
# ---------------------------------------------------------------------------


def _make_source(n_bits: int = 100000, seed: int = 42) -> NoiseSource:
    rng = np.random.default_rng(seed)
    bits = rng.integers(0, 2, size=n_bits, dtype=np.uint8)
    return NoiseSource(bits)


def test_generate_circuits_random_angles_produces_valid_circuits() -> None:
    source = _make_source()
    circuits = generate_circuits(
        source, n_qubits=3, depth=4, n_circuits=2, strategy="random_angles"
    )

    assert len(circuits) == 2
    for c in circuits:
        assert c.n_qubits == 3
        assert c.depth == 4
        assert c.strategy == "random_angles"
        assert c.bits_consumed > 0
        assert len(c.provenance) == 4  # depth layers
        assert len(c.qasm) > 0
        assert c.circuit.num_qubits == 3


def test_generate_circuits_random_structure_produces_valid_circuits() -> None:
    source = _make_source()
    circuits = generate_circuits(
        source, n_qubits=4, depth=3, n_circuits=2, strategy="random_structure"
    )

    assert len(circuits) == 2
    for c in circuits:
        assert c.strategy == "random_structure"
        assert c.n_qubits == 4
        assert c.depth == 3
        assert c.bits_consumed > 0
        assert len(c.provenance) == 3


def test_generate_circuits_random_walk_produces_valid_circuits() -> None:
    source = _make_source()
    circuits = generate_circuits(
        source, n_qubits=4, depth=3, n_circuits=2, strategy="random_walk"
    )

    assert len(circuits) == 2
    for c in circuits:
        assert c.strategy == "random_walk"
        assert c.bits_consumed > 0


def test_generate_circuits_single_qubit_works() -> None:
    source = _make_source()
    circuits = generate_circuits(
        source, n_qubits=1, depth=2, n_circuits=1, strategy="random_angles"
    )
    assert len(circuits) == 1
    assert circuits[0].circuit.num_qubits == 1


def test_different_noise_produces_different_circuits() -> None:
    source1 = _make_source(seed=1)
    source2 = _make_source(seed=2)

    circuits1 = generate_circuits(
        source1, n_qubits=2, depth=2, n_circuits=1, strategy="random_angles"
    )
    circuits2 = generate_circuits(
        source2, n_qubits=2, depth=2, n_circuits=1, strategy="random_angles"
    )

    # QASM should differ because the noise-driven angles differ
    assert circuits1[0].qasm != circuits2[0].qasm


def test_same_noise_produces_same_circuits() -> None:
    source1 = _make_source(seed=99)
    source2 = _make_source(seed=99)

    circuits1 = generate_circuits(
        source1, n_qubits=2, depth=3, n_circuits=1, strategy="random_angles"
    )
    circuits2 = generate_circuits(
        source2, n_qubits=2, depth=3, n_circuits=1, strategy="random_angles"
    )

    assert circuits1[0].qasm == circuits2[0].qasm
    assert circuits1[0].bits_consumed == circuits2[0].bits_consumed


def test_generate_circuits_tracks_consumed_bits() -> None:
    source = _make_source()
    before = source.consumed

    circuits = generate_circuits(
        source, n_qubits=3, depth=4, n_circuits=2, strategy="random_angles"
    )

    after = source.consumed
    total_consumed = sum(c.bits_consumed for c in circuits)
    assert after - before == total_consumed
    assert total_consumed > 0


def test_generate_circuits_insufficient_bits_raises() -> None:
    source = NoiseSource(np.array([1, 0, 1], dtype=np.uint8))

    with pytest.raises(ValueError, match="bits remaining"):
        generate_circuits(
            source, n_qubits=4, depth=8, n_circuits=5, strategy="random_angles"
        )


def test_generate_circuits_invalid_strategy_raises() -> None:
    source = _make_source()
    with pytest.raises(ValueError, match="Unknown strategy"):
        generate_circuits(source, strategy="invalid_strategy")


def test_generate_circuits_invalid_params_raise() -> None:
    source = _make_source()
    with pytest.raises(ValueError, match="n_qubits must be positive"):
        generate_circuits(source, n_qubits=0)
    with pytest.raises(ValueError, match="depth must be positive"):
        generate_circuits(source, depth=0)
    with pytest.raises(ValueError, match="n_circuits must be positive"):
        generate_circuits(source, n_circuits=0)
    with pytest.raises(ValueError, match="angle_bits must be positive"):
        generate_circuits(source, angle_bits=0)


def test_bits_per_circuit_estimate_matches_actual_consumption() -> None:
    for strategy in ("random_angles", "random_structure", "random_walk"):
        source = _make_source()
        estimated = _bits_per_circuit(strategy, 4, 4, 10)
        circuits = generate_circuits(
            source, n_qubits=4, depth=4, n_circuits=1,
            strategy=strategy, angle_bits=10,
        )
        assert circuits[0].bits_consumed == estimated


def test_qasm_export_contains_gates() -> None:
    source = _make_source()
    circuits = generate_circuits(
        source, n_qubits=2, depth=2, n_circuits=1, strategy="random_angles"
    )
    qasm = circuits[0].qasm
    assert "qubit" in qasm.lower() or "qreg" in qasm.lower() or "OPENQASM" in qasm


def test_generated_circuit_to_dict() -> None:
    source = _make_source()
    circuits = generate_circuits(
        source, n_qubits=2, depth=2, n_circuits=1, strategy="random_angles"
    )
    d = circuits[0].to_dict()
    assert d["strategy"] == "random_angles"
    assert d["n_qubits"] == 2
    assert d["depth"] == 2
    assert "qasm" in d
    assert isinstance(d["provenance"], list)


# ---------------------------------------------------------------------------
# Simulation tests
# ---------------------------------------------------------------------------


def test_simulate_circuits_returns_measurement_counts() -> None:
    source = _make_source()
    circuits = generate_circuits(
        source, n_qubits=2, depth=2, n_circuits=2, strategy="random_angles"
    )
    counts = simulate_circuits(circuits, shots=100)
    assert len(counts) == 2
    for c in counts:
        assert isinstance(c, dict)
        # all keys should be binary strings of length n_qubits
        for key in c:
            assert all(ch in "01 " for ch in key)
        # total shots should sum to 100
        assert sum(c.values()) == 100


# ---------------------------------------------------------------------------
# End-to-end pipeline test
# ---------------------------------------------------------------------------


def test_run_quantum_pipeline_end_to_end(tmp_path: Path) -> None:
    entropy_dir = tmp_path / "entropy"
    _fake_entropy_output(entropy_dir, n_bits=50000)

    result = run_quantum_pipeline(
        entropy_dir,
        output_dir=tmp_path / "quantum_output",
        n_qubits=3,
        depth=4,
        n_circuits=3,
        strategy="random_angles",
        simulate=True,
        shots=64,
    )

    assert result.report_path.is_file()
    assert result.simulated is True
    assert len(result.circuits) == 3
    assert result.measurement_counts is not None
    assert len(result.measurement_counts) == 3

    # Check report content
    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert report["status"] == "complete"
    assert report["generation"]["strategy"] == "random_angles"
    assert report["generation"]["n_qubits"] == 3
    assert report["generation"]["n_circuits"] == 3
    assert report["simulation"]["performed"] is True
    assert len(report["circuits"]) == 3

    # Check QASM files were written
    qasm_dir = result.output_dir / "qasm"
    assert qasm_dir.is_dir()
    qasm_files = list(qasm_dir.glob("*.qasm"))
    assert len(qasm_files) == 3


def test_run_quantum_pipeline_without_simulation(tmp_path: Path) -> None:
    entropy_dir = tmp_path / "entropy"
    _fake_entropy_output(entropy_dir, n_bits=50000)

    result = run_quantum_pipeline(
        entropy_dir,
        output_dir=tmp_path / "quantum_output",
        n_qubits=2,
        depth=3,
        n_circuits=2,
        strategy="random_structure",
        simulate=False,
    )

    assert result.simulated is False
    assert result.measurement_counts is None
    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert report["simulation"]["performed"] is False


def test_run_quantum_pipeline_all_strategies(tmp_path: Path) -> None:
    for strategy in ("random_angles", "random_structure", "random_walk"):
        entropy_dir = tmp_path / f"entropy_{strategy}"
        _fake_entropy_output(entropy_dir, n_bits=50000)

        result = run_quantum_pipeline(
            entropy_dir,
            output_dir=tmp_path / f"quantum_{strategy}",
            n_qubits=3,
            depth=3,
            n_circuits=2,
            strategy=strategy,
            simulate=True,
            shots=32,
        )
        assert result.simulated is True
        assert len(result.circuits) == 2
        assert all(c.strategy == strategy for c in result.circuits)


# ---------------------------------------------------------------------------
# CLI argument parsing test
# ---------------------------------------------------------------------------


def test_quantum_cli_arguments_parse_correctly() -> None:
    from camera_noise.cli import build_parser

    args = build_parser().parse_args([
        "quantum",
        "some/entropy/dir",
        "--qubits", "6",
        "--depth", "12",
        "--circuits", "10",
        "--strategy", "random_walk",
        "--angle-bits", "8",
        "--simulate",
        "--shots", "2048",
    ])

    assert args.command == "quantum"
    assert args.entropy_dir == Path("some/entropy/dir")
    assert args.qubits == 6
    assert args.depth == 12
    assert args.circuits == 10
    assert args.strategy == "random_walk"
    assert args.angle_bits == 8
    assert args.simulate is True
    assert args.shots == 2048
