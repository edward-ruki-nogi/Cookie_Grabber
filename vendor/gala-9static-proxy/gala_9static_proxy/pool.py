"""
Пул двузначных чисел (01–99) для формирования порта прокси типа «9 static».
Регистрируется в multiprocessing.Manager и передаётся воркерам.
"""
import multiprocessing
import time


class ProxyPortPool:
    """
    Пул из 99 двузначных значений (01–99).
    - acquire() — блокируется до появления свободного числа, возвращает наименьшее доступное.
    - release(num) — освобождает число обратно в пул.
    """

    def __init__(self) -> None:
        self._used = multiprocessing.Array("i", 100)
        self._lock = multiprocessing.Lock()

    def acquire(self) -> str:
        while True:
            with self._lock:
                for i in range(1, 100):
                    if self._used[i] == 0:
                        self._used[i] = 1
                        return f"{i:02d}"
            time.sleep(0.05)

    def release(self, num: str) -> None:
        try:
            i = int(num)
        except (ValueError, TypeError):
            return
        if 1 <= i <= 99:
            with self._lock:
                self._used[i] = 0
