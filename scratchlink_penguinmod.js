(function (Scratch) {
  "use strict";

  const Cast = Scratch.Cast;

  class ScratchLink {
    constructor() {
      this.baseUrl = "__SCRATCHLINK_BASE_URL__";
      this.connectionId = "__SCRATCHLINK_CONNECTION_ID__";
      this.password = "__SCRATCHLINK_PASSWORD__";
      this.connected = false;
      this.lastError = "";
      this.mode = "classic";
      this.buffer = [];
    }

    getInfo() {
      return {
        id: "scratchlink",
        name: "ScratchLink",
        color1: "#2779bd",
        color2: "#1c5d8b",
        blocks: [
          {
            blockType: Scratch.BlockType.LABEL,
            text: "Connection"
          },
          {
            opcode: "connect",
            blockType: Scratch.BlockType.COMMAND,
            text: "connect"
          },
          {
            opcode: "changeConnectionLink",
            blockType: Scratch.BlockType.COMMAND,
            text: "change connection link to [LINK]",
            arguments: {
              LINK: {
                type: Scratch.ArgumentType.STRING,
                defaultValue: "__SCRATCHLINK_EXTENSION_URL__"
              }
            }
          },
          {
            opcode: "isConnected",
            blockType: Scratch.BlockType.BOOLEAN,
            text: "connected?"
          },
          {
            opcode: "getLastError",
            blockType: Scratch.BlockType.REPORTER,
            text: "last error"
          },
          {
            opcode: "setMode",
            blockType: Scratch.BlockType.COMMAND,
            text: "set mode [MODE]",
            arguments: {
              MODE: {
                type: Scratch.ArgumentType.STRING,
                menu: "runModes",
                defaultValue: "classic"
              }
            }
          },
          {
            opcode: "activateBuffer",
            blockType: Scratch.BlockType.COMMAND,
            text: "activate buffer"
          },
          {
            opcode: "clearBuffer",
            blockType: Scratch.BlockType.COMMAND,
            text: "clear buffer"
          },
          {
            blockType: Scratch.BlockType.LABEL,
            text: "Hosting"
          },
          {
            opcode: "openHostedDirectory",
            blockType: Scratch.BlockType.COMMAND,
            text: "open hosted directory [NAME]",
            arguments: {
              NAME: {
                type: Scratch.ArgumentType.STRING,
                defaultValue: "webhook"
              }
            }
          },
          {
            opcode: "closeHostedDirectory",
            blockType: Scratch.BlockType.COMMAND,
            text: "close hosted directory [NAME]",
            arguments: {
              NAME: {
                type: Scratch.ArgumentType.STRING,
                defaultValue: "webhook"
              }
            }
          },
          {
            opcode: "getHostedDirectoryLink",
            blockType: Scratch.BlockType.REPORTER,
            text: "hosted directory [NAME] link",
            arguments: {
              NAME: {
                type: Scratch.ArgumentType.STRING,
                defaultValue: "webhook"
              }
            }
          },
          {
            opcode: "getHostedDirectoryWaitingRequests",
            blockType: Scratch.BlockType.REPORTER,
            text: "waiting requests under directory [NAME]",
            arguments: {
              NAME: {
                type: Scratch.ArgumentType.STRING,
                defaultValue: "webhook"
              }
            }
          },
          {
            opcode: "respondToHostedRequest",
            blockType: Scratch.BlockType.COMMAND,
            text: "respond to request [REQUEST_ID] status [STATUS] headers [HEADERS] body [BODY]",
            arguments: {
              REQUEST_ID: {
                type: Scratch.ArgumentType.STRING,
                defaultValue: "request-id"
              },
              STATUS: {
                type: Scratch.ArgumentType.NUMBER,
                defaultValue: 200
              },
              HEADERS: {
                type: Scratch.ArgumentType.STRING,
                defaultValue: "{\"Content-Type\":\"application/json\"}"
              },
              BODY: {
                type: Scratch.ArgumentType.STRING,
                defaultValue: "{\"ok\":true}"
              }
            }
          },
          {
            blockType: Scratch.BlockType.LABEL,
            text: "Roblox"
          },
          {
            opcode: "openRobloxGame",
            blockType: Scratch.BlockType.COMMAND,
            text: "open roblox game [ID]",
            arguments: {
              ID: {
                type: Scratch.ArgumentType.STRING,
                defaultValue: "9527099245"
              }
            }
          },
          {
            blockType: Scratch.BlockType.LABEL,
            text: "Files"
          },
          {
            opcode: "openFile",
            blockType: Scratch.BlockType.COMMAND,
            text: "open file [PATH]",
            arguments: {
              PATH: {
                type: Scratch.ArgumentType.STRING,
                defaultValue: "Documents"
              }
            }
          },
          {
            opcode: "getFilesUnderFolder",
            blockType: Scratch.BlockType.REPORTER,
            text: "files under folder [FOLDER]",
            arguments: {
              FOLDER: {
                type: Scratch.ArgumentType.STRING,
                defaultValue: "Documents"
              }
            }
          },
          {
            opcode: "readFile",
            blockType: Scratch.BlockType.REPORTER,
            text: "read file [PATH]",
            arguments: {
              PATH: {
                type: Scratch.ArgumentType.STRING,
                defaultValue: "example.txt"
              }
            }
          },
          {
            opcode: "writeFile",
            blockType: Scratch.BlockType.COMMAND,
            text: "write [TEXT] to file [PATH]",
            arguments: {
              TEXT: {
                type: Scratch.ArgumentType.STRING,
                defaultValue: "hello"
              },
              PATH: {
                type: Scratch.ArgumentType.STRING,
                defaultValue: "example.txt"
              }
            }
          },
          {
            opcode: "createFolder",
            blockType: Scratch.BlockType.COMMAND,
            text: "create folder [PATH]",
            arguments: {
              PATH: {
                type: Scratch.ArgumentType.STRING,
                defaultValue: "New Folder"
              }
            }
          },
          {
            opcode: "destroyFolder",
            blockType: Scratch.BlockType.COMMAND,
            text: "destroy folder [PATH]",
            arguments: {
              PATH: {
                type: Scratch.ArgumentType.STRING,
                defaultValue: "New Folder"
              }
            }
          },
          {
            opcode: "openApp",
            blockType: Scratch.BlockType.COMMAND,
            text: "open app [APP]",
            arguments: {
              APP: {
                type: Scratch.ArgumentType.STRING,
                defaultValue: "Notepad"
              }
            }
          },
          {
            blockType: Scratch.BlockType.LABEL,
            text: "Screen"
          },
          {
            opcode: "getScreenInfo",
            blockType: Scratch.BlockType.REPORTER,
            text: "screen [SCREEN] info",
            arguments: {
              SCREEN: { type: Scratch.ArgumentType.NUMBER, defaultValue: 1 }
            }
          },
          {
            opcode: "getScreenCapture",
            blockType: Scratch.BlockType.REPORTER,
            text: "screen [SCREEN] png uri",
            arguments: {
              SCREEN: { type: Scratch.ArgumentType.NUMBER, defaultValue: 1 }
            }
          },
          {
            opcode: "getAllScreensCapture",
            blockType: Scratch.BlockType.REPORTER,
            text: "all screens png uri"
          },
          {
            blockType: Scratch.BlockType.LABEL,
            text: "Mouse State"
          },
          {
            opcode: "getMouse",
            blockType: Scratch.BlockType.REPORTER,
            text: "mouse position"
          },
          {
            blockType: Scratch.BlockType.LABEL,
            text: "Mouse Actions"
          },
          {
            opcode: "moveMouseTo",
            blockType: Scratch.BlockType.COMMAND,
            text: "teleport mouse to x: [X] y: [Y] in [SECONDS] secs",
            arguments: {
              X: { type: Scratch.ArgumentType.NUMBER, defaultValue: 200 },
              Y: { type: Scratch.ArgumentType.NUMBER, defaultValue: 200 },
              SECONDS: { type: Scratch.ArgumentType.NUMBER, defaultValue: 0 }
            }
          },
          {
            opcode: "moveMouseBy",
            blockType: Scratch.BlockType.COMMAND,
            text: "move mouse by dx: [DX] dy: [DY] in [SECONDS] secs",
            arguments: {
              DX: { type: Scratch.ArgumentType.NUMBER, defaultValue: 50 },
              DY: { type: Scratch.ArgumentType.NUMBER, defaultValue: 50 },
              SECONDS: { type: Scratch.ArgumentType.NUMBER, defaultValue: 0.2 }
            }
          },
          {
            opcode: "mouseDown",
            blockType: Scratch.BlockType.COMMAND,
            text: "hold mouse [BUTTON]",
            arguments: {
              BUTTON: {
                type: Scratch.ArgumentType.STRING,
                menu: "mouseButtons"
              }
            }
          },
          {
            opcode: "mouseUp",
            blockType: Scratch.BlockType.COMMAND,
            text: "release mouse [BUTTON]",
            arguments: {
              BUTTON: {
                type: Scratch.ArgumentType.STRING,
                menu: "mouseButtons"
              }
            }
          },
          {
            opcode: "mouseClick",
            blockType: Scratch.BlockType.COMMAND,
            text: "click mouse [BUTTON] [CLICKS] times",
            arguments: {
              BUTTON: {
                type: Scratch.ArgumentType.STRING,
                menu: "mouseButtons"
              },
              CLICKS: { type: Scratch.ArgumentType.NUMBER, defaultValue: 1 }
            }
          },
          {
            opcode: "mouseMultiClick",
            blockType: Scratch.BlockType.COMMAND,
            text: "[COUNT] click mouse [BUTTON]",
            arguments: {
              COUNT: {
                type: Scratch.ArgumentType.STRING,
                menu: "multiClickCounts",
                defaultValue: "double"
              },
              BUTTON: {
                type: Scratch.ArgumentType.STRING,
                menu: "mouseButtons"
              }
            }
          },
          {
            blockType: Scratch.BlockType.LABEL,
            text: "Keyboard"
          },
          {
            opcode: "keyDown",
            blockType: Scratch.BlockType.COMMAND,
            text: "hold key [KEY]",
            arguments: {
              KEY: {
                type: Scratch.ArgumentType.STRING,
                menu: "keyboardKeys",
                defaultValue: "space"
              }
            }
          },
          {
            opcode: "keyUp",
            blockType: Scratch.BlockType.COMMAND,
            text: "release key [KEY]",
            arguments: {
              KEY: {
                type: Scratch.ArgumentType.STRING,
                menu: "keyboardKeys",
                defaultValue: "space"
              }
            }
          },
          {
            opcode: "keyPress",
            blockType: Scratch.BlockType.COMMAND,
            text: "press key [KEY]",
            arguments: {
              KEY: {
                type: Scratch.ArgumentType.STRING,
                menu: "keyboardKeys",
                defaultValue: "enter"
              }
            }
          },
          {
            opcode: "hotkey",
            blockType: Scratch.BlockType.COMMAND,
            text: "press key combo [MOD1] [MOD2] [KEY]",
            arguments: {
              MOD1: {
                type: Scratch.ArgumentType.STRING,
                menu: "modifierKeys",
                defaultValue: "ctrl"
              },
              MOD2: {
                type: Scratch.ArgumentType.STRING,
                menu: "modifierKeys",
                defaultValue: "shift"
              },
              KEY: {
                type: Scratch.ArgumentType.STRING,
                menu: "keyboardKeys",
                defaultValue: "esc"
              }
            }
          },
          {
            opcode: "typeText",
            blockType: Scratch.BlockType.COMMAND,
            text: "type text [TEXT]",
            arguments: {
              TEXT: {
                type: Scratch.ArgumentType.STRING,
                defaultValue: "hello"
              }
            }
          },
          {
            opcode: "waitSeconds",
            blockType: Scratch.BlockType.COMMAND,
            text: "wait on host [SECONDS] secs",
            arguments: {
              SECONDS: { type: Scratch.ArgumentType.NUMBER, defaultValue: 0.1 }
            }
          }
        ],
        menus: {
          mouseButtons: {
            acceptReporters: true,
            items: ["left", "middle", "right"]
          },
          multiClickCounts: {
            acceptReporters: true,
            items: ["double", "triple"]
          },
          keyboardKeys: {
            acceptReporters: true,
            items: [
              "enter",
              "space",
              "backspace",
              "tab",
              "escape",
              "up",
              "down",
              "left",
              "right",
              "shift",
              "ctrl",
              "alt",
              "win",
              "a",
              "b",
              "c",
              "d",
              "e",
              "f",
              "g",
              "h",
              "i",
              "j",
              "k",
              "l",
              "m",
              "n",
              "o",
              "p",
              "q",
              "r",
              "s",
              "t",
              "u",
              "v",
              "w",
              "x",
              "y",
              "z",
              "0",
              "1",
              "2",
              "3",
              "4",
              "5",
              "6",
              "7",
              "8",
              "9",
              "f1",
              "f2",
              "f3",
              "f4",
              "f5",
              "f6",
              "f7",
              "f8",
              "f9",
              "f10",
              "f11",
              "f12"
            ]
          },
          modifierKeys: {
            acceptReporters: true,
            items: ["none", "ctrl", "shift", "alt", "win"]
          },
          runModes: {
            acceptReporters: true,
            items: ["classic", "buffer"]
          }
        }
      };
    }

    async request(path, options = {}) {
      const response = await fetch(`${this.baseUrl}${path}`, {
        method: options.method || "GET",
        headers: {
          "Content-Type": "application/json",
          "X-ScratchLink-Connection": this.connectionId,
          "X-ScratchLink-Password": this.password
        },
        body: options.body ? JSON.stringify(options.body) : undefined
      });

      if (!response.ok) {
        const text = await response.text();
        throw new Error(text || `HTTP ${response.status}`);
      }

      return response.json();
    }

    parseConnectionLink(link) {
      const trimmed = String(link || "").trim();
      if (!trimmed) {
        throw new Error("Connection link is required");
      }

      let url;
      try {
        url = new URL(trimmed);
      } catch (error) {
        throw new Error("Connection link must be a full ScratchLink extension URL");
      }

      const match = url.pathname.match(/^(.*)\/extension\/([^/]+)\.js$/i);
      if (!match) {
        throw new Error("Use the full extension link copied from the ScratchLink app");
      }

      const password = url.searchParams.get("password");
      if (!password) {
        throw new Error("That connection link is missing its password");
      }

      const prefix = match[1] || "";
      return {
        baseUrl: `${url.origin}${prefix}`,
        connectionId: match[2],
        password
      };
    }

    async loadConnectionInfo(link) {
      const connection = this.parseConnectionLink(link);
      const response = await fetch(`${connection.baseUrl}/health`, {
        method: "GET",
        headers: {
          "Content-Type": "application/json",
          "X-ScratchLink-Connection": connection.connectionId,
          "X-ScratchLink-Password": connection.password
        }
      });

      if (!response.ok) {
        const text = await response.text();
        throw new Error(text || `HTTP ${response.status}`);
      }

      return connection;
    }

    async connect() {
      this.lastError = "";
      try {
        await this.request("/health");
        this.connected = true;
      } catch (error) {
        this.connected = false;
        this.lastError = String(error);
      }
    }

    async changeConnectionLink(args) {
      const previousBaseUrl = this.baseUrl;
      const previousConnectionId = this.connectionId;
      const previousPassword = this.password;
      this.lastError = "";
      try {
        const connection = await this.loadConnectionInfo(args.LINK);
        this.baseUrl = connection.baseUrl;
        this.connectionId = connection.connectionId;
        this.password = connection.password;
        await this.request("/health");
        this.connected = true;
      } catch (error) {
        this.baseUrl = previousBaseUrl;
        this.connectionId = previousConnectionId;
        this.password = previousPassword;
        this.connected = false;
        this.lastError = String(error);
      }
    }

    isConnected() {
      return this.connected;
    }

    getLastError() {
      return this.lastError;
    }

    setMode(args) {
      const mode = String(args.MODE || "classic").trim().toLowerCase();
      this.mode = mode === "buffer" ? "buffer" : "classic";
    }

    async activateBuffer() {
      if (!this.buffer.length) {
        return;
      }

      const actions = this.buffer.slice();
      try {
        await this.request("/batch", {
          method: "POST",
          body: { actions }
        });
        this.buffer = [];
        this.connected = true;
        this.lastError = "";
      } catch (error) {
        this.connected = false;
        this.lastError = String(error);
      }
    }

    clearBuffer() {
      this.buffer = [];
    }

    async openRobloxGame(args) {
      await this.runAction("/roblox/open-game", {
        id: String(args.ID || "").trim()
      }, "roblox.openGame");
    }

    async openHostedDirectory(args) {
      await this.safeCommand("/hosted-directories/open", {
        name: String(args.NAME || "").trim()
      });
    }

    async closeHostedDirectory(args) {
      await this.safeCommand("/hosted-directories/close", {
        name: String(args.NAME || "").trim()
      });
    }

    getHostedDirectoryLink(args) {
      const name = String(args.NAME || "").trim();
      if (!name) {
        return "";
      }
      return `${this.baseUrl}/directory/${encodeURIComponent(name)}`;
    }

    async getHostedDirectoryWaitingRequests(args) {
      const name = encodeURIComponent(String(args.NAME || "").trim());
      return this.readAsJsonString(`/hosted-directories/waiting?name=${name}`);
    }

    async respondToHostedRequest(args) {
      await this.safeCommand("/hosted-directories/respond", {
        request_id: String(args.REQUEST_ID || "").trim(),
        status: Math.max(100, Math.floor(Cast.toNumber(args.STATUS) || 200)),
        headers: String(args.HEADERS || "{}"),
        body: String(args.BODY || "")
      });
    }

    async openFile(args) {
      await this.safeCommand("/file/open", {
        path: String(args.PATH || "").trim()
      });
    }

    async getFilesUnderFolder(args) {
      const folder = encodeURIComponent(String(args.FOLDER || "").trim());
      return this.readAsJsonString(`/files/list?path=${folder}`);
    }

    async readFile(args) {
      try {
        const path = encodeURIComponent(String(args.PATH || "").trim());
        const data = await this.request(`/files/read?path=${path}`);
        this.connected = true;
        this.lastError = "";
        return String(data.text || "");
      } catch (error) {
        this.connected = false;
        this.lastError = String(error);
        return "";
      }
    }

    async writeFile(args) {
      await this.safeCommand("/files/write", {
        path: String(args.PATH || "").trim(),
        text: String(args.TEXT || "")
      });
    }

    async createFolder(args) {
      await this.safeCommand("/folders/create", {
        path: String(args.PATH || "").trim()
      });
    }

    async destroyFolder(args) {
      await this.safeCommand("/folders/destroy", {
        path: String(args.PATH || "").trim()
      });
    }

    async openApp(args) {
      await this.safeCommand("/app/open", {
        name: String(args.APP || "").trim()
      });
    }

    async getScreenInfo(args) {
      const screenNumber = Math.max(1, Math.floor(Cast.toNumber(args.SCREEN)));
      return this.readAsJsonString(`/screen/info/${screenNumber}`);
    }

    async getScreenCapture(args) {
      const screenNumber = Math.max(1, Math.floor(Cast.toNumber(args.SCREEN)));
      return this.readAsPngUri(`/screen/${screenNumber}`);
    }

    async getAllScreensCapture() {
      return this.readAsPngUri("/screen/all");
    }

    async getMouse() {
      return this.readAsJsonString("/mouse");
    }

    async moveMouseTo(args) {
      await this.runAction("/mouse/move", {
        x: Cast.toNumber(args.X),
        y: Cast.toNumber(args.Y),
        duration: Cast.toNumber(args.SECONDS)
      }, "mouse.move");
    }

    async moveMouseBy(args) {
      await this.runAction("/mouse/move-by", {
        dx: Cast.toNumber(args.DX),
        dy: Cast.toNumber(args.DY),
        duration: Cast.toNumber(args.SECONDS)
      }, "mouse.moveBy");
    }

    async mouseDown(args) {
      await this.runAction("/mouse/down", {
        button: String(args.BUTTON || "left").toLowerCase()
      }, "mouse.down");
    }

    async mouseUp(args) {
      await this.runAction("/mouse/up", {
        button: String(args.BUTTON || "left").toLowerCase()
      }, "mouse.up");
    }

    async mouseClick(args) {
      await this.runAction("/mouse/click", {
        button: String(args.BUTTON || "left").toLowerCase(),
        clicks: Math.max(1, Cast.toNumber(args.CLICKS))
      }, "mouse.click");
    }

    async mouseMultiClick(args) {
      const countName = String(args.COUNT || "double").trim().toLowerCase();
      const clicks = countName === "triple" ? 3 : 2;
      await this.runAction("/mouse/click", {
        button: String(args.BUTTON || "left").toLowerCase(),
        clicks
      }, "mouse.click");
    }

    async keyDown(args) {
      await this.runAction("/keyboard/down", {
        key: String(args.KEY || "").trim().toLowerCase()
      }, "keyboard.down");
    }

    async keyUp(args) {
      await this.runAction("/keyboard/up", {
        key: String(args.KEY || "").trim().toLowerCase()
      }, "keyboard.up");
    }

    async keyPress(args) {
      await this.runAction("/keyboard/press", {
        key: String(args.KEY || "").trim().toLowerCase()
      }, "keyboard.press");
    }

    async hotkey(args) {
      const keys = [args.MOD1, args.MOD2, args.KEY]
        .map(part => String(part || "").trim().toLowerCase())
        .filter(part => part && part !== "none");
      await this.runAction("/keyboard/hotkey", { keys }, "keyboard.hotkey");
    }

    async typeText(args) {
      await this.runAction("/keyboard/write", {
        text: String(args.TEXT || "")
      }, "keyboard.write");
    }

    async waitSeconds(args) {
      await this.runAction("/wait", {
        seconds: Cast.toNumber(args.SECONDS)
      }, "wait");
    }

    async readAsJsonString(path) {
      try {
        const data = await this.request(path);
        this.connected = true;
        this.lastError = "";
        return JSON.stringify(data);
      } catch (error) {
        this.connected = false;
        this.lastError = String(error);
        return "";
      }
    }

    async readAsPngUri(path) {
      try {
        const data = await this.request(path);
        this.connected = true;
        this.lastError = "";
        if (!data.imageBase64) {
          return "";
        }
        return `data:image/png;base64,${data.imageBase64}`;
      } catch (error) {
        this.connected = false;
        this.lastError = String(error);
        return "";
      }
    }

    async safeCommand(path, body) {
      try {
        await this.request(path, {
          method: "POST",
          body
        });
        this.connected = true;
        this.lastError = "";
      } catch (error) {
        this.connected = false;
        this.lastError = String(error);
      }
    }

    async runAction(path, body, actionType) {
      if (this.mode === "buffer") {
        this.buffer.push({ type: actionType, payload: body });
        return;
      }
      await this.safeCommand(path, body);
    }
  }

  Scratch.extensions.register(new ScratchLink());
})(Scratch);
