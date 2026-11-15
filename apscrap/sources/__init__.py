from .casasapo import CasaSapo
from .era import Era
from .imovirtual import Imovirtual
from .kw import Kw
from .remax import Remax

SOURCES = {s.name: s for s in [Imovirtual(), Era(), Remax(), Kw(), CasaSapo()]}

# The portals crawled by `apscrap sources`, in the order they run. Casa Sapo
# goes last: it is slow (45 s a page) and the others should not wait on it.
EXTRA_SOURCES = ["era", "remax", "kw", "casasapo"]
