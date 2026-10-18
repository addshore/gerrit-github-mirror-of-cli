package files

import (
	"strings"
	"testing"

	"gitlab.wikimedia.org/repos/releng/cli/mount"
)

func TestXdebugDefaultsRemainDebugOnlyAndAutomatic(t *testing.T) {
	for _, service := range []string{"mediawiki", "mediawiki-jobrunner"} {
		t.Run(service, func(t *testing.T) {
			content, err := mount.DevContent.ReadFile(mount.DevEmbedPath + "/compose/" + service + "/compose.yml")
			if err != nil {
				t.Fatal(err)
			}
			for _, setting := range []string{
				"XDEBUG_MODE=${MEDIAWIKI_XDEBUG_MODE:-debug}",
				"XDEBUG_CONFIG=${MEDIAWIKI_XDEBUG_CONFIG:-client_host=xdebug://gateway}",
			} {
				if !strings.Contains(string(content), setting) {
					t.Errorf("missing safe overridable default %q", setting)
				}
			}
		})
	}
}
