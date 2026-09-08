"""Generate quantum circuits driven by camera-sensor noise bitstreams.

This module bridges the entropy-extraction pipeline to quantum computing
by consuming the bitstream files produced by ``analyze_entropy`` and using
the noise bits to parameterise quantum circuits via Qiskit.

Requires the ``[quantum]`` optional dependency group::

    pip install -e ".[quantum]"
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

try:
    from qiskit import QuantumCircuit, qasm3
    from qiskit_aer import AerSimulator

    _QISKIT_AVAILABLE = True
except ImportError:  # pragma: no cover
    _QISKIT_AVAILABLE = False


def _require_qiskit() -> None:
    if not _QISKIT_AVAILABLE:
        raise ImportError(
            "Qiskit is required for quantum circuit generation. "
            "Install it with:  pip install -e \".[quantum]\""
        )


# ---------------------------------------------------------------------------
# Noise source — reads bits from bitstream files
# ---------------------------------------------------------------------------

class NoiseSource:
    """Stateful reader that yields bits and floats from a noise bitstream.

    Parameters
    ----------
    bits : np.ndarray
        One-dimensional uint8 array where each element is 0 or 1.
    """

    def __init__(self, bits: np.ndarray) -> None:
        self._bits = np.asarray(bits, dtype=np.uint8).ravel()
        if self._bits.size == 0:
            raise ValueError("Noise source must contain at least one bit")
        self._position = 0
        self._consumed = 0

    @classmethod
    def from_symbol_file(cls, path: Path | str) -> "NoiseSource":
        """Load a one-bit-per-byte symbol file (``*.symbols.bin``)."""
        data = Path(path).read_bytes()
        bits = np.frombuffer(data, dtype=np.uint8)
        return cls(bits)

    @classmethod
    def from_packed_file(cls, path: Path | str, meaningful_bits: int | None = None) -> "NoiseSource":
        """Load a packed binary file (``*.bin``, MSB first)."""
        data = Path(path).read_bytes()
        packed = np.frombuffer(data, dtype=np.uint8)
        bits = np.unpackbits(packed, bitorder="big")
        if meaningful_bits is not None:
            bits = bits[:meaningful_bits]
        return cls(bits)

    @classmethod
    def from_entropy_report(cls, entropy_dir: Path | str, method: str | None = None) -> "NoiseSource":
        """Load the recommended (or specified) bitstream from an entropy output directory.

        Parameters
        ----------
        entropy_dir : Path
            The output directory from ``analyze_entropy``, containing
            ``entropy_report.json`` and a ``bitstreams/`` subdirectory.
        method : str, optional
            The extraction method to use.  When *None*, uses the
            ``recommended_diagnostic_candidate`` from the entropy report.
        """
        entropy_dir = Path(entropy_dir)
        report_path = entropy_dir / "entropy_report.json"
        if not report_path.is_file():
            raise FileNotFoundError(f"No entropy report found at {report_path}")
        with report_path.open("r", encoding="utf-8") as handle:
            report = json.load(handle)

        if method is None:
            method = report.get("bitstream_comparison", {}).get(
                "recommended_diagnostic_candidate"
            )
        if method is None:
            raise ValueError(
                "No recommended extraction method found in the entropy report; "
                "specify --method explicitly"
            )
        exports = report.get("bitstream_exports", {})
        if method not in exports:
            available = sorted(exports.keys())
            raise ValueError(
                f"Method '{method}' not found in bitstream exports; "
                f"available: {available}"
            )
        export = exports[method]
        symbol_path = entropy_dir / export["one_bit_per_byte_binary"]
        return cls.from_symbol_file(symbol_path)

    @property
    def remaining(self) -> int:
        """Number of unconsumed bits remaining."""
        return len(self._bits) - self._position

    @property
    def consumed(self) -> int:
        """Total number of bits consumed so far."""
        return self._consumed

    @property
    def total(self) -> int:
        """Total number of bits in the source."""
        return len(self._bits)

    def take_bits(self, count: int) -> np.ndarray:
        """Consume and return *count* bits as a uint8 array of 0/1 values.

        Raises ``ValueError`` if fewer than *count* bits remain.
        """
        if count <= 0:
            return np.empty(0, dtype=np.uint8)
        if self.remaining < count:
            raise ValueError(
                f"Noise source exhausted: requested {count} bits "
                f"but only {self.remaining} remain "
                f"(consumed {self._consumed} of {self.total})"
            )
        result = self._bits[self._position : self._position + count].copy()
        self._position += count
        self._consumed += count
        return result

    def take_int(self, n_bits: int) -> int:
        """Consume *n_bits* bits and return them as an unsigned integer."""
        bits = self.take_bits(n_bits)
        value = 0
        for bit in bits:
            value = (value << 1) | int(bit)
        return value

    def take_float(self, n_bits: int = 10, low: float = 0.0, high: float = 1.0) -> float:
        """Consume *n_bits* bits and return a float mapped to [low, high].

        The integer formed by the bits is linearly mapped to the range.
        With 10 bits the resolution is ~0.001.
        """
        raw = self.take_int(n_bits)
        max_val = (1 << n_bits) - 1
        normalised = raw / max_val if max_val > 0 else 0.0
        return low + normalised * (high - low)

    def take_angle(self, n_bits: int = 10) -> float:
        """Consume *n_bits* bits and return a rotation angle in [0, 2*pi)."""
        return self.take_float(n_bits, 0.0, 2.0 * math.pi)


# ---------------------------------------------------------------------------
# Circuit generation strategies
# ---------------------------------------------------------------------------

_SINGLE_GATES = ("rx", "ry", "rz", "h", "x", "z", "s", "t")


def _apply_gate(circuit: Any, gate_index: int, qubit: int, angle: float) -> str:
    """Apply one of the available single-qubit gates and return the gate name."""
    gate = _SINGLE_GATES[gate_index % len(_SINGLE_GATES)]
    if gate == "rx":
        circuit.rx(angle, qubit)
    elif gate == "ry":
        circuit.ry(angle, qubit)
    elif gate == "rz":
        circuit.rz(angle, qubit)
    elif gate == "h":
        circuit.h(qubit)
    elif gate == "x":
        circuit.x(qubit)
    elif gate == "z":
        circuit.z(qubit)
    elif gate == "s":
        circuit.s(qubit)
    elif gate == "t":
        circuit.t(qubit)
    return gate


def _build_random_angles(
    source: NoiseSource, n_qubits: int, depth: int, angle_bits: int
) -> tuple[Any, list[dict[str, Any]]]:
    """Fixed Ry + CNOT layers; camera noise sets only rotation angles."""
    _require_qiskit()
    circuit = QuantumCircuit(n_qubits, n_qubits)
    provenance: list[dict[str, Any]] = []
    for layer in range(depth):
        layer_record: dict[str, Any] = {"layer": layer, "gates": []}
        for qubit in range(n_qubits):
            angle = source.take_angle(angle_bits)
            circuit.ry(angle, qubit)
            layer_record["gates"].append(
                {"gate": "ry", "qubit": qubit, "angle": angle}
            )
        for qubit in range(0, n_qubits - 1, 2):
            offset = layer % 2
            control = qubit + offset
            target = qubit + offset + 1
            if target < n_qubits:
                circuit.cx(control, target)
                layer_record["gates"].append(
                    {"gate": "cx", "control": control, "target": target}
                )
        provenance.append(layer_record)
    circuit.measure(range(n_qubits), range(n_qubits))
    return circuit, provenance


def _build_random_structure(
    source: NoiseSource, n_qubits: int, depth: int, angle_bits: int
) -> tuple[Any, list[dict[str, Any]]]:
    """Camera noise picks both single-qubit gate type and rotation angles."""
    _require_qiskit()
    circuit = QuantumCircuit(n_qubits, n_qubits)
    provenance: list[dict[str, Any]] = []
    n_gate_bits = 3  # 8 gate types -> 3 bits
    for layer in range(depth):
        layer_record: dict[str, Any] = {"layer": layer, "gates": []}
        for qubit in range(n_qubits):
            gate_idx = source.take_int(n_gate_bits)
            angle = source.take_angle(angle_bits)
            gate_name = _apply_gate(circuit, gate_idx, qubit, angle)
            layer_record["gates"].append(
                {"gate": gate_name, "qubit": qubit, "angle": angle, "gate_index": gate_idx}
            )
        # Entangling: noise picks which adjacent pair gets a CX
        if n_qubits >= 2:
            pair_bit = source.take_int(1)
            offset = pair_bit
            for qubit in range(offset, n_qubits - 1, 2):
                circuit.cx(qubit, qubit + 1)
                layer_record["gates"].append(
                    {"gate": "cx", "control": qubit, "target": qubit + 1}
                )
        provenance.append(layer_record)
    circuit.measure(range(n_qubits), range(n_qubits))
    return circuit, provenance


def _build_random_walk(
    source: NoiseSource, n_qubits: int, depth: int, angle_bits: int
) -> tuple[Any, list[dict[str, Any]]]:
    """Noise selects random single-qubit gates and random entangling pairs."""
    _require_qiskit()
    circuit = QuantumCircuit(n_qubits, n_qubits)
    provenance: list[dict[str, Any]] = []
    n_gate_bits = 3
    qubit_bits = max(1, int(math.ceil(math.log2(n_qubits)))) if n_qubits > 1 else 1
    for layer in range(depth):
        layer_record: dict[str, Any] = {"layer": layer, "gates": []}
        # Random single-qubit gate on each qubit
        for qubit in range(n_qubits):
            gate_idx = source.take_int(n_gate_bits)
            angle = source.take_angle(angle_bits)
            gate_name = _apply_gate(circuit, gate_idx, qubit, angle)
            layer_record["gates"].append(
                {"gate": gate_name, "qubit": qubit, "angle": angle}
            )
        # Random entangling: pick a control and target qubit from noise
        if n_qubits >= 2:
            control = source.take_int(qubit_bits) % n_qubits
            target = source.take_int(qubit_bits) % n_qubits
            if target == control:
                target = (control + 1) % n_qubits
            circuit.cx(control, target)
            layer_record["gates"].append(
                {"gate": "cx", "control": control, "target": target}
            )
        provenance.append(layer_record)
    circuit.measure(range(n_qubits), range(n_qubits))
    return circuit, provenance


_STRATEGIES = {
    "random_angles": _build_random_angles,
    "random_structure": _build_random_structure,
    "random_walk": _build_random_walk,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GeneratedCircuit:
    """A quantum circuit generated from camera noise, with provenance."""

    circuit: Any  # QuantumCircuit (typed as Any to avoid import errors)
    strategy: str
    n_qubits: int
    depth: int
    bits_consumed: int
    provenance: list[dict[str, Any]]
    qasm: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "n_qubits": self.n_qubits,
            "depth": self.depth,
            "bits_consumed": self.bits_consumed,
            "provenance": self.provenance,
            "qasm": self.qasm,
        }


@dataclass(frozen=True)
class QuantumResult:
    """Result of quantum circuit generation and optional simulation."""

    output_dir: Path
    report_path: Path
    circuits: tuple[GeneratedCircuit, ...]
    simulated: bool
    measurement_counts: tuple[dict[str, int], ...] | None


def _bits_per_circuit(
    strategy: str, n_qubits: int, depth: int, angle_bits: int
) -> int:
    """Estimate the number of noise bits consumed by one circuit."""
    if strategy == "random_angles":
        return depth * n_qubits * angle_bits
    elif strategy == "random_structure":
        per_layer = n_qubits * (3 + angle_bits) + (1 if n_qubits >= 2 else 0)
        return depth * per_layer
    elif strategy == "random_walk":
        qubit_bits = max(1, int(math.ceil(math.log2(n_qubits)))) if n_qubits > 1 else 1
        per_layer = n_qubits * (3 + angle_bits) + (2 * qubit_bits if n_qubits >= 2 else 0)
        return depth * per_layer
    raise ValueError(f"Unknown strategy: {strategy}")


def generate_circuits(
    source: NoiseSource,
    *,
    n_qubits: int = 4,
    depth: int = 8,
    n_circuits: int = 5,
    strategy: str = "random_angles",
    angle_bits: int = 10,
) -> list[GeneratedCircuit]:
    """Generate quantum circuits driven by camera-noise bits.

    Parameters
    ----------
    source : NoiseSource
        A bit source loaded from the entropy pipeline output.
    n_qubits : int
        Number of qubits per circuit.
    depth : int
        Number of gate layers per circuit.
    n_circuits : int
        How many circuits to produce.
    strategy : str
        One of ``"random_angles"``, ``"random_structure"``, ``"random_walk"``.
    angle_bits : int
        Number of noise bits per rotation angle (resolution ~2pi / 2^n).

    Returns
    -------
    list[GeneratedCircuit]
        Generated circuits with provenance tracking.

    Raises
    ------
    ValueError
        If the source has too few bits for the requested circuits.
    ImportError
        If Qiskit is not installed.
    """
    _require_qiskit()
    if strategy not in _STRATEGIES:
        raise ValueError(
            f"Unknown strategy '{strategy}'; choose from {sorted(_STRATEGIES)}"
        )
    if n_qubits < 1:
        raise ValueError("n_qubits must be positive")
    if depth < 1:
        raise ValueError("depth must be positive")
    if n_circuits < 1:
        raise ValueError("n_circuits must be positive")
    if angle_bits < 1:
        raise ValueError("angle_bits must be positive")

    estimated = _bits_per_circuit(strategy, n_qubits, depth, angle_bits) * n_circuits
    if source.remaining < estimated:
        raise ValueError(
            f"Noise source has {source.remaining} bits remaining, but "
            f"{n_circuits} circuits x {depth} layers x {n_qubits} qubits "
            f"with strategy '{strategy}' requires ~{estimated} bits"
        )

    builder = _STRATEGIES[strategy]
    circuits: list[GeneratedCircuit] = []
    for _ in range(n_circuits):
        consumed_before = source.consumed
        circuit, provenance = builder(source, n_qubits, depth, angle_bits)
        consumed_after = source.consumed
        qasm_str = qasm3.dumps(circuit)
        circuits.append(
            GeneratedCircuit(
                circuit=circuit,
                strategy=strategy,
                n_qubits=n_qubits,
                depth=depth,
                bits_consumed=consumed_after - consumed_before,
                provenance=provenance,
                qasm=qasm_str,
            )
        )
    return circuits


def simulate_circuits(
    circuits: list[GeneratedCircuit],
    shots: int = 1024,
) -> list[dict[str, int]]:
    """Run circuits on the Qiskit Aer simulator and return measurement counts.

    Parameters
    ----------
    circuits : list[GeneratedCircuit]
        Circuits to simulate.
    shots : int
        Number of measurement shots per circuit.

    Returns
    -------
    list[dict[str, int]]
        Measurement outcome counts for each circuit.
    """
    _require_qiskit()
    simulator = AerSimulator()
    results: list[dict[str, int]] = []
    for generated in circuits:
        job = simulator.run(generated.circuit, shots=shots)
        counts = job.result().get_counts()
        results.append(dict(counts))
    return results


def run_quantum_pipeline(
    entropy_dir: Path | str,
    *,
    output_dir: Path | str | None = None,
    method: str | None = None,
    n_qubits: int = 4,
    depth: int = 8,
    n_circuits: int = 5,
    strategy: str = "random_angles",
    angle_bits: int = 10,
    simulate: bool = False,
    shots: int = 1024,
) -> QuantumResult:
    """End-to-end pipeline: load entropy -> generate circuits -> optionally simulate.

    Parameters
    ----------
    entropy_dir : Path
        Output directory from ``analyze_entropy``.
    output_dir : Path, optional
        Where to write the quantum report and QASM files.
        Defaults to ``entropy_dir / "quantum"``.
    method : str, optional
        Bit-extraction method to use; defaults to the entropy report's
        recommended candidate.
    n_qubits, depth, n_circuits, strategy, angle_bits
        Forwarded to :func:`generate_circuits`.
    simulate : bool
        If *True*, run circuits on the Aer simulator.
    shots : int
        Simulator shots per circuit (only when *simulate* is True).

    Returns
    -------
    QuantumResult
    """
    from camera_noise.storage import utc_now_iso, write_json_atomic

    _require_qiskit()
    entropy_dir = Path(entropy_dir)
    output = Path(output_dir) if output_dir else entropy_dir / "quantum"
    output.mkdir(parents=True, exist_ok=False)

    source = NoiseSource.from_entropy_report(entropy_dir, method=method)
    circuits = generate_circuits(
        source,
        n_qubits=n_qubits,
        depth=depth,
        n_circuits=n_circuits,
        strategy=strategy,
        angle_bits=angle_bits,
    )

    measurement_counts: tuple[dict[str, int], ...] | None = None
    if simulate:
        counts = simulate_circuits(circuits, shots=shots)
        measurement_counts = tuple(counts)

    # Write QASM files
    qasm_dir = output / "qasm"
    qasm_dir.mkdir()
    qasm_paths: list[str] = []
    for index, generated in enumerate(circuits):
        qasm_path = qasm_dir / f"circuit_{index:03d}.qasm"
        qasm_path.write_text(generated.qasm, encoding="utf-8")
        qasm_paths.append(str(qasm_path.relative_to(output)))

    # Build and write report
    report_path = output / "quantum_report.json"
    report: dict[str, Any] = {
        "schema_version": 1,
        "status": "complete",
        "started_utc": utc_now_iso(),
        "source_entropy_dir": str(entropy_dir),
        "extraction_method": method or "recommended_candidate",
        "noise_source": {
            "total_bits": source.total,
            "bits_consumed": source.consumed,
            "bits_remaining": source.remaining,
        },
        "generation": {
            "strategy": strategy,
            "n_qubits": n_qubits,
            "depth": depth,
            "n_circuits": n_circuits,
            "angle_bits": angle_bits,
            "bits_per_circuit_estimate": _bits_per_circuit(
                strategy, n_qubits, depth, angle_bits
            ),
        },
        "circuits": [
            {
                "index": index,
                "bits_consumed": c.bits_consumed,
                "qasm_file": qasm_paths[index],
                "provenance": c.provenance,
            }
            for index, c in enumerate(circuits)
        ],
        "simulation": {
            "performed": simulate,
            "shots": shots if simulate else None,
            "measurement_counts": (
                [dict(c) for c in measurement_counts]
                if measurement_counts
                else None
            ),
        },
        "caveats": [
            "Camera noise bitstreams are diagnostic candidates, not certified entropy sources.",
            "These circuits are driven by processed webcam output, not proven quantum randomness.",
            "The noise quality depends on the extraction method, camera settings, and environmental conditions.",
            "This is an experimental research tool, not a cryptographically secure random circuit generator.",
        ],
    }
    write_json_atomic(report_path, report)

    return QuantumResult(
        output_dir=output,
        report_path=report_path,
        circuits=tuple(circuits),
        simulated=simulate,
        measurement_counts=measurement_counts,
    )
