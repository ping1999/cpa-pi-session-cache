CC ?= cc
CFLAGS ?= -O2 -Wall -Wextra -Werror -std=c11 -fPIC -fvisibility=hidden -fstack-protector-strong
TARGET = dist/pi-cpa-session-cache.so
SOURCES = src/plugin.c src/sha256.c vendor/cjson/cJSON.c

.PHONY: all test clean
all: $(TARGET)
$(TARGET): $(SOURCES) src/sha256.h vendor/cjson/cJSON.h
	mkdir -p dist
	$(CC) $(CFLAGS) -Ivendor/cjson -shared $(SOURCES) -Wl,-z,relro,-z,now -o $@
test: all
	python3 -m unittest discover -s tests -v
clean:
	rm -rf dist
