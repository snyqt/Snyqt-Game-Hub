/* ====================================================================
   SNYQT Game Hub — 前端交互脚本
   - 主题切换（localStorage 记忆，默认深色）
   - 轮播（自动播放 + 手动导航）
   - 上传表单字段联动 + 封面预览
   - 评论 / 购买 / 下载 AJAX
   - 卡片 staggered 动画
   - toast 通知
   ==================================================================== */
(function () {
    'use strict';

    /* ---------- 主题切换 ---------- */
    function initTheme() {
        var saved = localStorage.getItem('snyqt-theme');
        if (!saved) saved = 'dark'; // 默认深色
        document.documentElement.setAttribute('data-theme', saved);

        var btn = document.querySelector('.theme-toggle');
        if (!btn) return;
        btn.addEventListener('click', function () {
            var cur = document.documentElement.getAttribute('data-theme') || 'dark';
            var next = cur === 'dark' ? 'light' : 'dark';
            document.documentElement.setAttribute('data-theme', next);
            localStorage.setItem('snyqt-theme', next);
        });
    }

    /* ---------- 用户下拉菜单 ---------- */
    function initUserMenu() {
        var menu = document.querySelector('.user-menu');
        if (!menu) return;
        var btn = menu.querySelector('.avatar-btn');
        if (!btn) return;
        btn.addEventListener('click', function (e) {
            e.stopPropagation();
            menu.classList.toggle('open');
        });
        document.addEventListener('click', function () {
            menu.classList.remove('open');
        });
    }

    /* ---------- 首页轮播 ---------- */
    function initHero() {
        var hero = document.querySelector('.hero');
        if (!hero) return;
        var track = hero.querySelector('.hero-track');
        var slides = hero.querySelectorAll('.hero-slide');
        var dots = hero.querySelectorAll('.hero-dots button');
        var prev = hero.querySelector('.hero-nav .prev');
        var next = hero.querySelector('.hero-nav .next');
        if (!track || !slides.length) return;

        var idx = 0;
        var timer = null;

        function go(i) {
            idx = (i + slides.length) % slides.length;
            track.style.transform = 'translateX(-' + (idx * 100) + '%)';
            dots.forEach(function (d, k) { d.classList.toggle('active', k === idx); });
        }

        function play() {
            stop();
            timer = setInterval(function () { go(idx + 1); }, 5000);
        }
        function stop() { if (timer) clearInterval(timer); }

        if (next) next.addEventListener('click', function () { go(idx + 1); play(); });
        if (prev) prev.addEventListener('click', function () { go(idx - 1); play(); });
        dots.forEach(function (d, k) {
            d.addEventListener('click', function () { go(k); play(); });
        });
        hero.addEventListener('mouseenter', stop);
        hero.addEventListener('mouseleave', play);
        go(0); play();
    }

    /* ---------- 卡片 staggered 动画 ---------- */
    function initStaggered() {
        var cards = document.querySelectorAll('.game-card');
        cards.forEach(function (c, i) {
            c.style.animationDelay = (Math.min(i, 16) * 0.045) + 's';
        });
    }

    /* ---------- 上传表单 ---------- */
    function initUploadForm() {
        var form = document.querySelector('#upload-form');
        if (!form) return;

        var typeSelect = form.querySelector('[name="hosting_type"]');
        var htmlFields = form.querySelector('.html-fields');
        var pyFields = form.querySelector('.python-fields');

        function syncFields() {
            var v = typeSelect ? typeSelect.value : 'html';
            if (htmlFields) htmlFields.classList.toggle('hidden', v !== 'html');
            if (pyFields) pyFields.classList.toggle('hidden', v !== 'python');
        }
        if (typeSelect) {
            typeSelect.addEventListener('change', syncFields);
            syncFields();
        }

        // 封面预览
        var coverInput = form.querySelector('[name="cover"]');
        if (coverInput) {
            coverInput.addEventListener('change', function () {
                var preview = form.querySelector('.cover-preview');
                if (preview && coverInput.files && coverInput.files[0]) {
                    preview.innerHTML = '';
                    var img = document.createElement('img');
                    img.src = URL.createObjectURL(coverInput.files[0]);
                    preview.appendChild(img);
                    preview.classList.remove('hidden');
                }
            });
        }

        // 截图预览
        var shotsInput = form.querySelector('[name="screenshots"]');
        if (shotsInput) {
            shotsInput.addEventListener('change', function () {
                var preview = form.querySelector('.shots-preview');
                if (!preview) return;
                preview.innerHTML = '';
                if (!shotsInput.files) return;
                Array.prototype.forEach.call(shotsInput.files, function (f) {
                    var img = document.createElement('img');
                    img.src = URL.createObjectURL(f);
                    preview.appendChild(img);
                });
                preview.classList.remove('hidden');
            });
        }

        // 提交（含进度）
        form.addEventListener('submit', function (e) {
            e.preventDefault();
            var fd = new FormData(form);
            var progress = form.querySelector('.upload-progress');
            var bar = progress ? progress.querySelector('.bar') : null;
            var submitBtn = form.querySelector('[type="submit"]');
            if (progress) progress.classList.add('show');
            if (bar) bar.style.width = '0%';
            if (submitBtn) submitBtn.disabled = true;

            var xhr = new XMLHttpRequest();
            xhr.open('POST', form.getAttribute('action') || '/api/games/upload');
            // 大文件上传超时设置（10 分钟）
            xhr.timeout = 10 * 60 * 1000;
            xhr.upload.addEventListener('progress', function (e) {
                if (e.lengthComputable && bar) {
                    var pct = Math.round((e.loaded / e.total) * 100);
                    bar.style.width = pct + '%';
                }
            });
            xhr.onload = function () {
                if (submitBtn) submitBtn.disabled = false;
                var data;
                try { data = JSON.parse(xhr.responseText); } catch (_) { data = null; }
                if (xhr.status >= 200 && xhr.status < 300 && data && data.success) {
                    toast(data.message || '上传成功', 'success');
                    setTimeout(function () { window.location.reload(); }, 900);
                } else {
                    // 显示后端返回的真实错误信息（如"Python 启动命令不能为空"）
                    var msg = (data && data.message) || '上传失败 (HTTP ' + xhr.status + ')';
                    toast(msg, 'error');
                }
            };
            xhr.onerror = function () {
                if (submitBtn) submitBtn.disabled = false;
                // 网络层错误：连接被中断。显示更详细的诊断信息
                toast('网络连接中断（HTTP ' + xhr.status + '）。可能原因：文件过大、网络波动或服务器重启', 'error');
            };
            xhr.ontimeout = function () {
                if (submitBtn) submitBtn.disabled = false;
                toast('上传超时，请检查网络或减小文件体积', 'error');
            };
            xhr.send(fd);
        });
    }

    /* ---------- 评分输入 ---------- */
    function initStarInput() {
        var wrap = document.querySelector('.stars-input');
        if (!wrap) return;
        var buttons = wrap.querySelectorAll('button');
        var input = document.querySelector('[name="rating-value"]');
        function paint(n) {
            buttons.forEach(function (b, k) {
                b.classList.toggle('on', k < n);
            });
        }
        buttons.forEach(function (b, k) {
            b.addEventListener('click', function () {
                if (input) input.value = (k + 1);
                paint(k + 1);
            });
        });
    }

    /* ---------- 评论提交 ---------- */
    function initReviewForm() {
        var form = document.querySelector('#review-form');
        if (!form) return;
        var gid = form.getAttribute('data-game-id');
        if (!gid) return;

        form.addEventListener('submit', function (e) {
            e.preventDefault();
            var rating = (form.querySelector('[name="rating-value"]') || {}).value;
            var comment = (form.querySelector('[name="comment"]') || {}).value || '';
            if (!rating) { toast('请先选择评分', 'warning'); return; }

            var fd = new FormData();
            fd.append('rating', rating);
            fd.append('comment', comment);

            fetch('/api/games/' + gid + '/review', { method: 'POST', body: fd })
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    if (data.success) {
                        toast(data.message || '评分成功', 'success');
                        setTimeout(function () { window.location.reload(); }, 700);
                    } else {
                        toast(data.message || '评分失败', 'error');
                    }
                })
                .catch(function () { toast('网络错误', 'error'); });
        });
    }

    /* ---------- 购买/获取 ---------- */
    function initPurchaseButtons() {
        document.querySelectorAll('[data-action="purchase"]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                var gid = btn.getAttribute('data-game-id');
                if (!gid) return;
                btn.disabled = true;
                fetch('/api/games/' + gid + '/purchase', { method: 'POST' })
                    .then(function (r) { return r.json(); })
                    .then(function (data) {
                        if (data.success) {
                            toast(data.message || '已加入游戏库', 'success');
                            setTimeout(function () { window.location.reload(); }, 800);
                        } else {
                            toast(data.message || '操作失败', 'error');
                            btn.disabled = false;
                        }
                    })
                    .catch(function () {
                        toast('网络错误', 'error');
                        btn.disabled = false;
                    });
            });
        });
    }

    /* ---------- 管理面板操作 ---------- */
    function initAdminActions() {
        // 权限级别修改
        document.querySelectorAll('[data-perm-select]').forEach(function (sel) {
            sel.addEventListener('change', function () {
                var uid = sel.getAttribute('data-user-id');
                var level = sel.value;
                postJSON('/api/admin/permission', { user_id: uid, level: level }, '权限已更新');
            });
        });

        // Python 审核
        document.querySelectorAll('[data-py-review]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                var rid = btn.getAttribute('data-py-review');
                var action = btn.getAttribute('data-action');
                var reason = prompt(action === 'reject' ? '拒绝理由（可选）' : '批准说明（可选）', '') || '';
                postJSON('/api/admin/python-review/' + rid, { action: action, reason: reason }, '审核完成');
            });
        });

        // 封禁/解禁
        document.querySelectorAll('[data-ban]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                var gid = btn.getAttribute('data-ban');
                var action = btn.getAttribute('data-action'); // ban / unban
                if (!confirm(action === 'ban' ? '确认封禁该游戏？' : '确认解禁该游戏？')) return;
                fetch('/api/admin/games/' + gid + '/' + action, { method: 'POST' })
                    .then(function (r) { return r.json(); })
                    .then(function (data) {
                        toast(data.message || '操作完成', data.success ? 'success' : 'error');
                        if (data.success) setTimeout(function () { window.location.reload(); }, 700);
                    })
                    .catch(function () { toast('网络错误', 'error'); });
            });
        });

        // view 资格
        document.querySelectorAll('[data-view-elig]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                var gid = btn.getAttribute('data-view-elig');
                var action = btn.getAttribute('data-action'); // grant / revoke
                fetch('/api/admin/games/' + gid + '/view-eligibility', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ action: action })
                })
                    .then(function (r) { return r.json(); })
                    .then(function (data) {
                        toast(data.message || '操作完成', data.success ? 'success' : 'error');
                        if (data.success) setTimeout(function () { window.location.reload(); }, 700);
                    })
                    .catch(function () { toast('网络错误', 'error'); });
            });
        });

        // 权限申请审核（批准 / 拒绝）
        document.querySelectorAll('[data-app-review]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                var uid = btn.getAttribute('data-app-review');
                var action = btn.getAttribute('data-action'); // approve / reject
                if (!confirm(action === 'approve' ? '确认批准该权限申请？' : '确认拒绝该权限申请？')) return;
                postJSON('/api/admin/applications/' + uid, { action: action }, '审核完成');
            });
        });

        /* ---------- 板块折叠 ---------- */
        document.querySelectorAll('[data-admin-block] [data-toggle]').forEach(function (header) {
            header.addEventListener('click', function () {
                var block = header.closest('[data-admin-block]');
                if (block) block.classList.toggle('collapsed');
            });
        });

        /* ---------- 板块内搜索（实时过滤表格行） ---------- */
        document.querySelectorAll('[data-admin-block] [data-search-input]').forEach(function (input) {
            input.addEventListener('input', function () {
                var block = input.closest('[data-admin-block]');
                if (!block) return;
                var keyword = input.value.trim().toLowerCase();
                var rows = block.querySelectorAll('[data-search-row]');
                var visibleCount = 0;
                rows.forEach(function (row) {
                    var text = (row.getAttribute('data-search-text') || '').toLowerCase();
                    var match = text.indexOf(keyword) !== -1;
                    row.style.display = match ? '' : 'none';
                    if (match) visibleCount++;
                });
                // 无匹配时显示提示
                if (block.querySelector('.admin-no-match')) {
                    block.classList.toggle('no-match', visibleCount === 0);
                }
            });
            // 阻止输入框点击事件冒泡到折叠头部
            input.addEventListener('click', function (e) { e.stopPropagation(); });
        });
    }

    /* ---------- 权限申请表单 ---------- */
    function initApplyForm() {
        var submitBtn = document.querySelector('[data-apply-submit]');
        if (!submitBtn) return;

        submitBtn.addEventListener('click', function () {
            var selected = document.querySelector('input[name="level"]:checked');
            if (!selected) { toast('请先选择要申请的权限', 'warning'); return; }
            var level = selected.value;
            submitBtn.disabled = true;
            fetch('/api/permissions/apply', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ level: level })
            })
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    if (data.success) {
                        toast(data.message || '申请已提交', 'success');
                        setTimeout(function () { window.location.reload(); }, 900);
                    } else {
                        toast(data.message || '申请失败', 'error');
                        submitBtn.disabled = false;
                    }
                })
                .catch(function () {
                    toast('网络错误', 'error');
                    submitBtn.disabled = false;
                });
        });
    }

    function postJSON(url, body, okMsg) {
        fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                toast(data.message || okMsg || '操作完成', data.success ? 'success' : 'error');
                if (data.success) setTimeout(function () { window.location.reload(); }, 700);
            })
            .catch(function () { toast('网络错误', 'error'); });
    }

    /* ---------- toast ---------- */
    function toast(msg, type) {
        var container = document.querySelector('.toast-container');
        if (!container) {
            container = document.createElement('div');
            container.className = 'toast-container';
            document.body.appendChild(container);
        }
        var el = document.createElement('div');
        el.className = 'toast ' + (type || 'info');
        el.textContent = msg;
        container.appendChild(el);
        // 强制重绘触发动画
        el.offsetWidth;
        el.classList.add('show');
        setTimeout(function () {
            el.classList.remove('show');
            setTimeout(function () { el.remove(); }, 400);
        }, 3200);
    }
    window.snyqtToast = toast;

    /* ---------- 闪光数字滚动（积分） ---------- */
    function initPointsAnim() {
        var chip = document.querySelector('.points-chip');
        if (!chip) return;
        chip.addEventListener('mouseenter', function () {
            chip.style.transform = 'scale(1.06)';
            setTimeout(function () { chip.style.transform = ''; }, 220);
        });
    }

    /* ---------- 入口 ---------- */
    document.addEventListener('DOMContentLoaded', function () {
        initTheme();
        initUserMenu();
        initHero();
        initStaggered();
        initUploadForm();
        initStarInput();
        initReviewForm();
        initPurchaseButtons();
        initAdminActions();
        initApplyForm();
        initPointsAnim();
    });
})();
