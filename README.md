# DFA simulator

Single-file DFA runner. Open `index.html` directly in a browser, fill in the form, and click **Run**.

## Input format

- **States Q**: comma-separated (e.g., `q0,q1`)
- **Alphabet Σ**: comma-separated (e.g., `a,b`)
- **Start state**: single state (e.g., `q0`)
- **Accepting states F**: comma-separated (e.g., `q1`)
- **Transitions δ**: one rule per line, format `state,symbol=nextState`  
  Example:
  ```
  q0,a=q1
  q0,b=q0
  q1,a=q0
  q1,b=q1
  ```

Missing transitions fall into a special dead state `__dead__`.  
The tool checks determinism and reports malformed inputs inline.

## Sample

- States: `q0,q1`  
- Alphabet: `a,b`  
- Start: `q0`  
- Accepting: `q1`  
- Transitions: as above  
- Input: `abba` → **REJECT**

## File

- `index.html` — single-file app (no build tools)

## Roadmap

- [ ] Visual editor for states & transitions  
- [ ] Step-by-step run (trace)  
- [ ] Export / import JSON  
- [ ] NFA / ε-NFA and DFA conversion  
- [ ] Minimization & equivalence checking