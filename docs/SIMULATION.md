# Simulation bench

The bench runs the real `ReportsServer` and `DatalakeClient` against a simulated data lake. The production data are synthetic (one robot, 240 cycles, a fixed seed) and no hardware is involved.

What it checks (`tests/test_simulation_bench.py`, job "Simulation bench"):

- a healthy lake gives exactly the counts and the quality of the synthetic shift, and the same input gives the same fingerprint twice;
- every failure of the lake ends in an error answer and never in a wrong number: an error status, a body that is not JSON, a JSON of the wrong shape, rows without the expected fields, a body shorter than its declared length, a stall longer than the client timeout, a lake that is still starting, a lake that refuses the token and a lake that goes away;
- after each failure the server keeps serving and gives the exact figures again once the lake is well;
- several readers at the same second get clean answers.

The evidence of this project is `simulator`. It says nothing about a real data lake under load or about real machines.
