"""
SyncFiles — Git Integration
Auto-commit, push, pull, status, diff via GitPython.
"""

import logging
from pathlib import Path
from datetime import datetime

logger = logging.getLogger('syncfiles.git')

try:
    import git
    HAS_GIT = True
except ImportError:
    HAS_GIT = False


class GitSync:
    """Git operations for sync workflow."""

    def __init__(self, repo_path, config=None):
        """
        repo_path: Path to git repository
        config: Config object (optional, for auto-commit settings)
        """
        self.repo_path = Path(repo_path)
        self.config = config
        self._repo = None

    @staticmethod
    def is_available():
        return HAS_GIT

    def is_repo(self):
        """Check if path is a git repository."""
        return (self.repo_path / '.git').exists()

    def init_repo(self):
        """Initialize a git repository."""
        if not HAS_GIT:
            raise RuntimeError("GitPython not installed. pip install GitPython")
        self._repo = git.Repo.init(self.repo_path)
        return True

    def open_repo(self):
        """Open existing repo."""
        if not HAS_GIT:
            raise RuntimeError("GitPython not installed")
        if not self.is_repo():
            raise RuntimeError(f"Not a git repo: {self.repo_path}")
        self._repo = git.Repo(self.repo_path)
        return True

    def status(self):
        """Get repository status."""
        self._require_repo()
        return {
            'branch': str(self._repo.active_branch),
            'is_dirty': self._repo.is_dirty(),
            'untracked': self._repo.untracked_files,
            'modified': [item.a_path for item in self._repo.index.diff(None)],
            'staged': [item.a_path for item in self._repo.index.diff('HEAD')] if self._repo.head.is_valid() else [],
            'has_remote': len(self._repo.remotes) > 0,
        }

    def add_all(self):
        """Stage all changes."""
        self._require_repo()
        self._repo.git.add(A=True)

    def commit(self, message=None):
        """Commit staged changes."""
        self._require_repo()
        if not self._repo.is_dirty() and not self._repo.untracked_files:
            return None  # Nothing to commit

        self.add_all()

        if not message:
            template = 'sync: {timestamp} — {files_changed} files'
            if self.config:
                template = self.config.get('git', 'commit_template') or template
            # Count changed files
            changed = len(self._repo.index.diff('HEAD')) if self._repo.head.is_valid() else len(self._repo.untracked_files)
            message = template.format(
                timestamp=datetime.now().strftime('%Y-%m-%d %H:%M'),
                files_changed=changed,
            )

        commit = self._repo.index.commit(message)
        logger.info(f"Committed: {commit.hexsha[:8]} — {message}")
        return {
            'sha': commit.hexsha,
            'message': message,
            'timestamp': datetime.now().isoformat(),
        }

    def push(self, remote='origin', branch=None):
        """Push to remote."""
        self._require_repo()
        if not self._repo.remotes:
            raise RuntimeError("No remotes configured")
        r = self._repo.remotes[remote]
        branch = branch or str(self._repo.active_branch)
        info = r.push(branch)
        logger.info(f"Pushed to {remote}/{branch}")
        return {'remote': remote, 'branch': branch}

    def pull(self, remote='origin', branch=None):
        """Pull from remote. Auto-stashes if dirty."""
        self._require_repo()
        stashed = False
        if self._repo.is_dirty():
            self._repo.git.stash()
            stashed = True
            logger.info("Auto-stashed dirty working tree")

        try:
            r = self._repo.remotes[remote]
            branch = branch or str(self._repo.active_branch)
            info = r.pull(branch)
            logger.info(f"Pulled from {remote}/{branch}")
        finally:
            if stashed:
                try:
                    self._repo.git.stash('pop')
                    logger.info("Restored stash")
                except Exception as e:
                    logger.warning(f"Stash pop conflict: {e}")

        return {'remote': remote, 'branch': branch, 'stashed': stashed}

    def diff(self, path=None):
        """Get diff output."""
        self._require_repo()
        if path:
            return self._repo.git.diff(path)
        return self._repo.git.diff()

    def log(self, limit=10):
        """Get recent commits."""
        self._require_repo()
        commits = []
        for c in self._repo.iter_commits(max_count=limit):
            commits.append({
                'sha': c.hexsha[:8],
                'message': c.message.strip(),
                'author': str(c.author),
                'date': datetime.fromtimestamp(c.committed_date).isoformat(),
            })
        return commits

    def test_connection(self):
        """Test remote connection."""
        try:
            self._require_repo()
            if not self._repo.remotes:
                return {'ok': False, 'error': 'No remotes configured'}
            remote = self._repo.remotes[0]
            remote.fetch(dry_run=True)
            return {'ok': True, 'remote': remote.name, 'url': list(remote.urls)[0]}
        except Exception as e:
            return {'ok': False, 'error': str(e)}

    def auto_sync(self):
        """Run auto-commit and push if configured."""
        if not self.config:
            return None

        results = {}

        if self.config.get('git', 'auto_commit'):
            commit_result = self.commit()
            if commit_result:
                results['commit'] = commit_result

                if self.config.get('git', 'auto_push'):
                    try:
                        push_result = self.push()
                        results['push'] = push_result
                    except Exception as e:
                        results['push_error'] = str(e)
                        logger.error(f"Auto-push failed: {e}")

        return results if results else None

    def _require_repo(self):
        if not self._repo:
            self.open_repo()
