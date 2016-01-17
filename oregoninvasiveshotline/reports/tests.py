import binascii
import codecs
import csv
import json
import os
import tempfile
from unittest.mock import Mock, patch

from datetime import timedelta
from django.conf import settings
from django.core.exceptions import NON_FIELD_ERRORS
from django.core.files.base import File
from django.core.urlresolvers import reverse
from django.db.models.signals import post_save
from django.test import TestCase
from django.utils import timezone

from model_mommy.mommy import make, prepare

from arcutils.test.user import UserMixin

from oregoninvasiveshotline.comments.forms import CommentForm
from oregoninvasiveshotline.comments.models import Comment
from oregoninvasiveshotline.images.models import Image
from oregoninvasiveshotline.species.models import Category, Severity, Species
from oregoninvasiveshotline.users.models import User

from .forms import InviteForm, ManagementForm, ReportForm, ReportSearchForm
from .models import Invite, Report, receiver__generate_icon
from .search_indexes import ReportIndex
from .views import _export


class SuppressPostSaveMixin:

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        post_save.disconnect(receiver__generate_icon, sender=Report)

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        post_save.connect(receiver__generate_icon, sender=Report)


class ReportTest(SuppressPostSaveMixin, TestCase):

    def setUp(self):
        self.report = Report()
        self.index = ReportIndex()
        self.index.clear()

    def tearDown(self):
        if self.report.pk is not None:
            self.report.delete()
        self.index.clear()

    def test_species(self):
        reported_species = make(Species)
        actual_species = make(Species)

        self.assertEqual(make(Report, actual_species=None, reported_species=reported_species).species, reported_species)
        self.assertEqual(make(Report, actual_species=actual_species, reported_species=reported_species).species, actual_species)
        self.assertEqual(make(Report, actual_species=None, reported_species=None).species, None)

    def test_category(self):
        reported_species = make(Species)
        actual_species = make(Species)

        self.assertEqual(
            make(Report, actual_species=None, reported_species=None, reported_category=reported_species.category).category,
            reported_species.category
        )
        self.assertEqual(make(Report, actual_species=actual_species, reported_species=reported_species).category, actual_species.category)

    def test_is_misidentified(self):
        reported_species = make(Species)
        actual_species = make(Species)

        # if they didn't identify the species, then it can't be misidentified
        self.assertEqual(make(Report, actual_species=None, reported_species=None).is_misidentified, False)
        # if the reported and actual species are the same, it's not misidentified
        self.assertEqual(make(Report, actual_species=actual_species, reported_species=actual_species).is_misidentified, False)
        # if the species differ, then it is misidentified
        self.assertEqual(make(Report, actual_species=actual_species, reported_species=reported_species).is_misidentified, True)

    def test_str(self):
        self.assertEqual("Foo", str(make(Report, actual_species=None, reported_species=None, reported_category=make(Category, name="Foo"))))
        self.assertEqual("Bar (Foo)", str(make(Report, actual_species=None, reported_species=make(Species, name="Bar", scientific_name="Foo"))))

    def test_image_url(self):
        report = make(Report)
        # it's a private image, so it shouldn't be the image_url
        image = make(Image, report=report, visibility=Image.PRIVATE)
        self.assertEqual(None, report.image_url)
        # this public image should be the image_url
        image = make(Image, report=report, visibility=Image.PUBLIC)
        self.assertEqual(settings.MEDIA_URL + "generated_thumbnails/" + str(image.pk) + ".png", report.image_url)

        Image.objects.all().delete()

        # private images on comments shouldn't be used for the image_url
        make(Image, comment=make(Comment, report=report), visibility=Image.PRIVATE, _quantity=2)
        self.assertEqual(None, report.image_url)
        # public images on comments can be used for the image_url
        image = make(Image, comment=make(Comment, report=report), visibility=Image.PUBLIC)
        self.assertEqual(settings.MEDIA_URL + "generated_thumbnails/" + str(image.pk) + ".png", report.image_url)

        # make sure the file got created
        self.assertTrue(os.path.exists(os.path.join(settings.MEDIA_ROOT, "generated_thumbnails", str(image.pk) + ".png")))


class TestReportIconGeneration(TestCase):

    def _make_category_icon(self):
        content = binascii.unhexlify(
            # Turtle icon encoded as hex
            b'89504e470d0a1a0a0000000d494844520000002000000025080600000023b7eb47000000d249444154588'
            b'5ed95410ec42008453f93b9191cbb9ecd59991082561cba980c6fd3a6c6f004a1405114455114bf063377'
            b'fdfc96d7c9a6de7b9a0445373073bfae0b0020220080d61ae975bb47afa708008095d08c350020a2658cb'
            b'08027a1453cb14733e0e1952645203b3870d005abe083dde04702d9bcbd8fb69522274a1168add1183622'
            b'9236f53cdc123073b76d65dfb398a676e7c65ba21db014884a9c04bf15d0121a4f6877f28505acc86afc5'
            b'a911d19b70b66ec9422f223028219589d74466a066cf08c0115be038327a7e37ff101afa37d185ce02898'
            b'0000000049454e44ae426082'
        )
        icon_dir = os.path.join(settings.MEDIA_ROOT, 'icons')
        fd, name = tempfile.mkstemp(dir=icon_dir, suffix='.png')
        os.write(fd, content)
        os.close(fd)
        icon_file = File(open(name, 'rb'), name)
        return icon_file

    def test_generate_icon_manually(self):
        report = make.prepare(
            Report,
            actual_species__severity__color='#ff8800',
            actual_species__category__icon=self._make_category_icon(),
        )
        self.assertFalse(os.path.exists(report.icon_path))
        report.generate_icon()
        self.assertTrue(os.path.exists(report.icon_path))
        # Clean up
        os.unlink(report.icon_path)

    def test_icon_is_generated_on_post_save_and_post_init_for_existing_reports(self):
        report = make(
            Report,
            actual_species__severity__color='#ff8800',
            actual_species__category__icon=self._make_category_icon(),
        )
        # The report was saved with a PK by make(), so it should have an
        # icon.
        self.assertTrue(os.path.exists(report.icon_path))
        # Remove the icon and verify that its icon is re-created the
        # next time the report is fetched.
        os.unlink(report.icon_path)
        Report.objects.get(pk=report.pk)
        self.assertTrue(os.path.exists(report.icon_path))
        # Clean up
        os.unlink(report.icon_path)


class ReportSearchFormTest(TestCase, UserMixin):

    def setUp(self):
        self.user = self.create_user(
            username="foo@example.com",
            password="foo",
            is_active=True,
            is_staff=True
        )
        self.report = Report()
        self.index = ReportIndex()
        self.index.clear()

    def tearDown(self):
        if self.report.pk is not None:
            self.report.delete()
        self.index.clear()

    def test_filter_by_claimed_reports(self):
        claimed_report = make(Report, claimed_by=self.user)
        unclaimed_report = make(Report, claimed_by=None)

        form = ReportSearchForm({
            "q": "",
            "claimed_by": "me",
        }, user=self.user)
        results = form.search()

        # Since form.search() returns a SearchQuerySet, we create from that a
        # list of reports so we can see if our desired reports are in the list.
        reports = list()
        for r in results:
            reports.append(r.object)

        self.assertIn(claimed_report, reports)
        self.assertNotIn(unclaimed_report, reports)
        self.assertEqual(len(reports), 1)

    def test_filter_by_unclaimed_reports(self):
        claimed_report = make(Report, claimed_by=self.user)
        unclaimed_report = make(Report, claimed_by=None)

        form = ReportSearchForm({
            "q": "",
            "claimed_by": "nobody",
        }, user=self.user)
        results = form.search()

        # Since form.search() returns a SearchQuerySet, we create from that a
        # list of reports so we can see if our desired reports are in the list.
        reports = list()
        for r in results:
            reports.append(r.object)

        self.assertIn(unclaimed_report, reports)
        self.assertNotIn(claimed_report, reports)
        self.assertEqual(len(reports), 1)

    def test_filter_by_archived_reports(self):
        archived_report = make(Report, is_archived=True)
        unarchived_report = make(Report, is_archived=False)

        form = ReportSearchForm({
            "q": "",
            "is_archived": "archived",
        }, user=self.user)
        results = form.search()

        reports = list()
        for r in results:
            reports.append(r.object)

        self.assertIn(archived_report, reports)
        self.assertNotIn(unarchived_report, reports)
        self.assertEqual(len(reports), 1)

    def test_filter_by_unarchived_reports(self):
        archived_report = make(Report, is_archived=True)
        unarchived_report = make(Report, is_archived=False)

        form = ReportSearchForm({
            "q": "",
            "is_archived": "notarchived",
        }, user=self.user)
        results = form.search()

        reports = list()
        for r in results:
            reports.append(r.object)

        self.assertIn(unarchived_report, reports)
        self.assertNotIn(archived_report, reports)
        self.assertEqual(len(reports), 1)

    def test_filter_by_public_reports(self):
        pub_report = make(Report, is_public=True)
        priv_report = make(Report, is_public=False)

        form = ReportSearchForm({
            "q": "",
            "is_public": "public",
        }, user=self.user)
        results = form.search()

        reports = list()
        for r in results:
            reports.append(r.object)

        self.assertIn(pub_report, reports)
        self.assertNotIn(priv_report, reports)
        self.assertEqual(len(reports), 1)

    def test_filter_by_not_public_reports(self):
        pub_report = make(Report, is_public=True)
        priv_report = make(Report, is_public=False)

        form = ReportSearchForm({
            "q": "",
            "is_public": "notpublic",
        }, user=self.user)
        results = form.search()

        reports = list()
        for r in results:
            reports.append(r.object)

        self.assertIn(priv_report, reports)
        self.assertNotIn(pub_report, reports)
        self.assertEqual(len(reports), 1)

    def test_filter_by_reports_user_was_invited_to(self):
        inviter = self.create_user(username="inviter@example.com")
        invited_report = make(Report, created_by=inviter)
        other_report = make(Report)
        make(Invite, user=self.user, created_by=inviter, report=invited_report)

        form = ReportSearchForm({
            "q": "",
            "source": "invited",
        }, user=self.user)
        results = form.search()

        reports = list()
        for r in results:
            reports.append(r.object)

        self.assertIn(invited_report, reports)
        self.assertNotIn(other_report, reports)
        self.assertEqual(len(reports), 1)

    def test_filter_by_reports_user_reported(self):
        my_report = make(Report, created_by=self.user)
        other_report = make(Report)

        form = ReportSearchForm({
            "q": "",
            "source": "reported",
        }, user=self.user, report_ids=[my_report.pk])
        results = form.search()

        reports = list()
        for r in results:
            reports.append(r.object)

        self.assertIn(my_report, reports)
        self.assertNotIn(other_report, reports)
        self.assertEqual(len(reports), 1)

    def test_sort_by_field_sorts_reports(self):
        now = timezone.now()
        make(Report, created_on=now - timedelta(days=1))
        make(Report, created_on=now)
        make(Report, created_on=now + timedelta(days=1))

        form = ReportSearchForm({
            "sort_by": "-created_on",
        }, user=self.user)
        results = form.search()

        reports = list()
        for r in results:
            reports.append(r.object)

        self.assertTrue(reports, Report.objects.all().order_by("-created_on"))

    def test_inactive_users_only_see_public_fields(self):
        self.user.is_active = False
        self.user.save()
        form = ReportSearchForm(self, user=self.user)
        form_fields = sorted(tuple(form.fields.keys()))
        public_fields = sorted(form.public_fields)
        self.assertEqual(form_fields, public_fields)

    def test_inactive_users_only_see_public_reports_and_reports_they_created(self):
        self.user.is_active = False
        self.user.save()
        pub_report = make(Report, is_public=True)
        priv_report = make(Report, is_public=False)
        my_report = make(Report, created_by=self.user)

        # Since we aren't creating reports through a view, manually assign the
        # created report to report_ids (already covered in view tests)
        form = ReportSearchForm({"q": ""}, user=self.user, report_ids=[my_report.pk])
        results = form.search()

        # Since form.search() returns a SearchQuerySet, we create from that a
        # list of reports so we can see if our desired reports are in the list.
        reports = list()
        for r in results:
            reports.append(r.object)

        # Ensure that only pub_report and my_report are in the list of reports
        self.assertIn(pub_report, reports)
        self.assertIn(my_report, reports)
        self.assertNotIn(priv_report, reports)
        self.assertEqual(len(reports), 2)


class CreateViewTest(TestCase):

    def setUp(self):
        self.index = ReportIndex()
        self.index.clear()

    def tearDown(self):
        self.index.clear()

    def test_get(self):
        c1 = make(Category)
        c2 = make(Category)
        s1 = make(Species, category=c1)
        s2 = make(Species, category=c1)
        make(Species, category=c2)
        response = self.client.get(reverse("reports-create"))
        self.assertEqual(response.status_code, 200)
        # make sure the category_id_to_species_id gets populated
        self.assertEqual(set(json.loads(response.context['category_id_to_species_id'])[str(c1.pk)]), set([s1.pk, s2.pk]))

    def test_post(self):
        data = {
            "location": "back ally",
            "point": "SRID=4326;POINT(-6.7236328125 8.61328125)",
            "reported_category": make(Category).pk,
            "description": "It was HUGE",
            "questions": "question",
            "prefix": "Dr.",
            "first_name": "John",
            "last_name": "Evil",
            "suffix": "PHD",
            "email": "john@example.com",
            "form-TOTAL_FORMS": "0",
            "form-INITIAL_FORMS": "0",
            "form-MIN_NUM_FORMS": "0",
            "form-MAX_NUM_FORMS": "1000",
        }

        response = self.client.post(reverse("reports-create"), data)
        self.assertRedirects(response, reverse("reports-detail", args=[Report.objects.order_by("-pk").first().pk]))
        session = self.client.session
        # make sure the report_ids in the session gets updated
        self.assertIn(Report.objects.order_by("-pk").first().pk, session['report_ids'])


class DetailViewTest(TestCase, UserMixin):

    def setUp(self):
        self.user = self.create_user(
            username="foo@example.com",
            password="foo",
            is_active=True
        )
        self.admin = self.create_user(
            username="admin@example.com",
            password="admin",
            is_active=True,
            is_staff=True
        )
        self.inactive_user = self.create_user(
            username="inactive@example.com",
            is_active=False
        )
        self.index = ReportIndex()
        self.index.clear()

    def tearDown(self):
        self.index.clear()

    def test_anonymous_users_cant_view_non_public_reports_and_is_prompted_to_login(self):
        report = make(Report, is_public=False)
        response = self.client.get(reverse("reports-detail", args=[report.pk]))
        self.assertRedirects(response, reverse("login") + "?next=" + reverse("reports-detail", args=[report.pk]))

    def test_anonymous_users_with_proper_session_state_can_view_non_public_reports(self):
        report = make(Report, is_public=False, created_by=self.inactive_user)
        session = self.client.session
        session['report_ids'] = [report.pk]
        session.save()
        response = self.client.get(reverse("reports-detail", args=[report.pk]))
        self.assertEqual(response.status_code, 200)

    def test_anonymous_users_with_proper_session_state_should_be_prompted_to_login_if_the_report_was_created_by_an_active_user(self):
        report = make(Report, is_public=False, created_by=self.user)
        session = self.client.session
        session['report_ids'] = [report.pk]
        session.save()
        response = self.client.get(reverse("reports-detail", args=[report.pk]))
        self.assertRedirects(response, reverse("login") + "?next=" + reverse("reports-detail", args=[report.pk]))

    def test_invited_experts_cannot_see_every_report(self):
        report = make(Report, is_public=False)
        # we set is_active to True just so self.client.login works, but we have
        # to set it back to False
        invited_expert = self.user
        self.client.login(email=invited_expert.email, password="foo")
        invited_expert.is_active = False
        invited_expert.save()

        # the expert hasn't been invited to this report, so it should trigger
        # permission denied
        response = self.client.get(reverse("reports-detail", args=[report.pk]))
        self.assertEqual(response.status_code, 403)

        # once we invite the expert, it should be ok
        make(Invite, user=invited_expert, report=report)
        response = self.client.get(reverse("reports-detail", args=[report.pk]))
        self.assertEqual(response.status_code, 200)

    def test_comment_form_dependent_on_the_can_create_comment_check(self):
        report = make(Report, is_public=True)
        with patch("oregoninvasiveshotline.reports.views.can_create_comment", return_value=True) as perm_check:
            with patch("oregoninvasiveshotline.reports.views.CommentForm"):
                response = self.client.get(reverse("reports-detail", args=[report.pk]))
        self.assertTrue(perm_check.called)
        self.assertNotEqual(None, response.context['comment_form'])

        with patch("oregoninvasiveshotline.reports.views.can_create_comment", return_value=False) as perm_check:
            with patch("oregoninvasiveshotline.reports.views.CommentForm"):
                response = self.client.get(reverse("reports-detail", args=[report.pk]))
        self.assertTrue(perm_check.called)
        self.assertEqual(None, response.context['comment_form'])

    def test_display_of_comments_for_each_permission_level(self):
        report = make(Report, is_public=True, created_by=self.inactive_user)
        public = make(Comment, report=report, visibility=Comment.PUBLIC)
        protected = make(Comment, report=report, visibility=Comment.PROTECTED)
        private = make(Comment, report=report, visibility=Comment.PRIVATE)

        # anonymous users should only be able to see public comments
        response = self.client.get(reverse("reports-detail", args=[report.pk]))
        self.assertIn(public.body, response.content.decode())
        self.assertNotIn(protected.body, response.content.decode())
        self.assertNotIn(private.body, response.content.decode())

        # the person who made the report should be allowed to see PROTECTED and PUBLIC comments
        session = self.client.session
        session['report_ids'] = [report.pk]
        session.save()
        response = self.client.get(reverse("reports-detail", args=[report.pk]))
        self.assertIn(public.body, response.content.decode())
        self.assertIn(protected.body, response.content.decode())
        self.assertNotIn(private.body, response.content.decode())

        # staffers should see everything
        self.client.login(email=self.user.email, password="foo")
        session = self.client.session
        session['report_ids'] = []
        session.save()
        response = self.client.get(reverse("reports-detail", args=[report.pk]))
        self.assertIn(public.body, response.content.decode())
        self.assertIn(protected.body, response.content.decode())
        self.assertIn(private.body, response.content.decode())

        # invited experts should see everything
        self.client.logout()
        invited_expert = self.user
        self.client.login(email=invited_expert.email, password="foo")
        invited_expert.is_active = False  # we just had to set this to True to make self.client.login work
        invited_expert.save()
        make(Invite, user=invited_expert, report=report)
        response = self.client.get(reverse("reports-detail", args=[report.pk]))
        self.assertIn(public.body, response.content.decode())
        self.assertIn(protected.body, response.content.decode())
        self.assertIn(private.body, response.content.decode())

    def test_create_comment(self):
        report = make(Report)
        self.client.login(email=self.user.email, password="foo")
        data = {
            "body": "foo",
            "visibility": Comment.PUBLIC,
            "form-TOTAL_FORMS": "0",
            "form-INITIAL_FORMS": "0",
            "form-MIN_NUM_FORMS": "0",
            "form-MAX_NUM_FORMS": "1000",
            "submit_flag": CommentForm.SUBMIT_FLAG
        }
        response = self.client.post(reverse("reports-detail", args=[report.pk]), data)
        self.assertRedirects(response, reverse("reports-detail", args=[report.pk]))
        self.assertEqual(1, Comment.objects.filter(report=report).count())

    def test_forms_are_none_for_anonymous_users(self):
        report = make(Report, is_public=True)
        response = self.client.get(reverse("reports-detail", args=[report.pk]))
        forms = [
            "comment_form",
            "image_formset",
            "invite_form",
            "management_form",
        ]
        for form in forms:
            self.assertEqual(None, response.context[form])

    def test_forms_are_initialized_for_admins(self):
        self.client.login(email=self.admin.email, password="admin")
        report = make(Report)
        response = self.client.get(reverse("reports-detail", args=[report.pk]))
        forms = [
            "comment_form",
            "image_formset",
            "invite_form",
            "management_form",
        ]
        for form in forms:
            self.assertNotEqual(None, response.context[form])

    def test_forms_filled_out(self):
        report = make(Report)
        self.client.login(email=self.admin.email, password="admin")

        with patch("oregoninvasiveshotline.reports.views.ManagementForm", SUBMIT_FLAG="foo") as m:
            data = {
                "submit_flag": ["foo"],
            }
            response = self.client.post(reverse("reports-detail", args=[report.pk]), data)
            m.assert_called_once_with(data, instance=report)
            self.assertTrue(m().save.called)
            self.assertRedirects(response, reverse("reports-detail", args=[report.pk]))

        # the InviteForm is slightly more complicated, so we need a special case for that
        with patch("oregoninvasiveshotline.reports.views.InviteForm", SUBMIT_FLAG="foo", save=Mock(return_value=Mock(already_invited=1))) as m:
            data = {
                "submit_flag": ["foo"],
            }
            response = self.client.post(reverse("reports-detail", args=[report.pk]), data)
            self.assertEqual(1, m.call_count)
            m().save.assert_called_once_with(user=self.admin, report=report, request=response.wsgi_request)
            self.assertRedirects(response, reverse("reports-detail", args=[report.pk]))


class ReportFormTest(TestCase):

    def setUp(self):
        self.index = ReportIndex()
        self.index.clear()

    def tearDown(self):
        self.index.clear()

    def test_reported_species_is_not_required(self):
        form = ReportForm({})
        self.assertFalse(form.is_valid())
        self.assertFalse(form.has_error("reported_species"))

    def test_save_creates_user_if_it_doesnt_exist(self):
        # the user doesn't exist, so he should be created when the form is saved
        form = ReportForm({
            "email": "foo@example.com",
            "first_name": "Foo",
            "last_name": "Bar",
            "prefix": "Mr.",
            "suffix": "PHD",
        })
        self.assertFalse(form.is_valid())
        report = prepare(Report, pk=1)
        pre_count = User.objects.count()
        request = Mock(build_absolute_uri=Mock(return_value=""))

        with patch("oregoninvasiveshotline.reports.forms.forms.ModelForm.save") as save:
            form.instance = report
            form.save(request=request)
            self.assertTrue(save.called)

        self.assertEqual(User.objects.count(), pre_count+1)
        user = User.objects.order_by("-pk").first()
        self.assertEqual(user.email, "foo@example.com")
        self.assertEqual(user.is_active, False)
        self.assertEqual(user.last_name, "Bar")
        self.assertEqual(user.pk, form.instance.created_by.pk)

        # the user already exists, so no record should be created
        pre_count = User.objects.count()
        form = ReportForm({
            "email": "FOO@eXaMplE.com",  # using odd casing here to ensure `icontains` is used
        })
        self.assertFalse(form.is_valid())
        pre_count = User.objects.count()
        with patch("oregoninvasiveshotline.reports.forms.forms.ModelForm.save") as save:
            form.instance = report
            form.save(request=request)
            self.assertTrue(save.called)

        self.assertEqual(User.objects.count(), pre_count)
        self.assertEqual(user.pk, form.instance.created_by.pk)

    def test_comment_is_added(self):
        form = ReportForm({
            "email": "foo@example.com",
            "first_name": "Foo",
            "last_name": "Bar",
            "questions": "hello world",
        })
        self.assertFalse(form.is_valid())
        report = make(Report)
        form.instance = report
        with patch("oregoninvasiveshotline.reports.forms.forms.ModelForm.save"):
            form.save(request=Mock(build_absolute_uri=Mock(return_value="")))

        self.assertEqual(Comment.objects.get(report=report).body, "hello world")


class ManagementFormTest(SuppressPostSaveMixin, TestCase):

    def setUp(self):
        self.index = ReportIndex()
        self.index.clear()

    def tearDown(self):
        self.index.clear()

    def test_species_and_category_initialized(self):
        species = make(Species)
        report = make(Report, reported_species=species, reported_category=species.category)
        form = ManagementForm(instance=report)
        self.assertEqual(form.initial['category'], species.category)
        self.assertEqual(form.initial['actual_species'], species)

    def test_field_widget_ids_match_expected_id_from_javascript(self):
        """
        The javascript for the category/species selector expects the ids for
        the category and species fields to be something particular
        """
        report = make(Report)
        form = ManagementForm(instance=report)
        self.assertEqual(form.fields['category'].widget.attrs['id'], 'id_reported_category')
        self.assertEqual(form.fields['actual_species'].widget.attrs['id'], 'id_reported_species')

    def test_either_a_new_species_is_entered_xor_an_existing_species_is_selected(self):
        report = make(Report)
        data = {
            "new_species": "Yeti",
            "actual_species": make(Species).pk
        }
        form = ManagementForm(data, instance=report)
        self.assertFalse(form.is_valid())
        self.assertTrue(form.has_error(NON_FIELD_ERRORS, code="species_contradiction"))

        data = {
            "new_species": "Yeti",
        }
        form = ManagementForm(data, instance=report)
        self.assertFalse(form.is_valid())
        self.assertFalse(form.has_error(NON_FIELD_ERRORS, code="species_contradiction"))

    def test_if_new_species_is_entered_severity_is_required(self):
        report = make(Report)
        data = {
            "new_species": "Yeti",
        }
        form = ManagementForm(data, instance=report)
        self.assertFalse(form.is_valid())
        self.assertTrue(form.has_error("severity", code="required"))

    def test_new_species_is_saved(self):
        report = make(Report)
        category = make(Category)
        severity = make(Severity)
        data = {
            "new_species": "Yeti",
            "category": category.pk,
            "severity": severity.pk
        }
        form = ManagementForm(data, instance=report)
        self.assertTrue(form.is_valid())
        form.save()
        species = Species.objects.get(name="Yeti", category=category)
        self.assertEqual(report.actual_species, species)

    def test_is_public_field_disabled_for_is_confidential_species(self):
        report = make(Report, actual_species__is_confidential=True)
        form = ManagementForm(instance=report, data={
            # even though this was submitted with a True-y value, the form
            # should override it so it is always False
            "is_public": 1,
            "edrr_status": 0,
            "category": make(Category).pk,
        })
        self.assertTrue(form.fields['is_public'].widget.attrs['disabled'])
        self.assertTrue(form.is_valid())
        form.save()
        # even though the data spoofed the is_public flag as True, it should still be false
        self.assertFalse(report.is_public)

    def test_settings_the_actual_species_to_a_confidential_species_raises_an_error_if_the_report_is_public_too(self):
        report = make(Report)
        form = ManagementForm(instance=report, data={
            "actual_species": make(Species, is_confidential=True).pk,
            "is_public": 1,
            "edrr_status": 0,
            "category": make(Category).pk,
        })
        self.assertFalse(form.is_valid())
        self.assertTrue(form.has_error(NON_FIELD_ERRORS, "species-confidential"))


class InviteFormTest(TestCase, UserMixin):

    def setUp(self):
        self.index = ReportIndex()
        self.index.clear()

    def tearDown(self):
        self.index.clear()

    def test_clean_emails(self):
        # test a few valid emails
        form = InviteForm({
            "emails": "foo@pdx.edu,bar@pdx.edu  ,  fog@pdx.edu,foo@pdx.edu"
        })
        self.assertTrue(form.is_valid())
        self.assertEqual(sorted(form.cleaned_data['emails']), sorted(["foo@pdx.edu", "bar@pdx.edu", "fog@pdx.edu"]))

        # test blank
        form = InviteForm({
            "emails": ""
        })
        self.assertFalse(form.is_valid())
        self.assertTrue(form.has_error("emails"))

        # test invalid email
        form = InviteForm({
            "emails": "invalid@@pdx.ads"
        })
        self.assertFalse(form.is_valid())
        self.assertTrue(form.has_error("emails"))

        # test valid and invalid
        form = InviteForm({
            "emails": "valid@pdx.edu, invalid@@pdx.ads"
        })
        self.assertFalse(form.is_valid())
        self.assertTrue(form.has_error("emails"))
        self.assertIn("invalid@@", str(form.errors))

    def test_save(self):
        """
        Ensure the Invite.create method is called, and that the InviteReport we
        get back is correct
        """
        form = InviteForm({
            "emails": "already_invited@pdx.edu,foo@pdx.edu",
            "body": "foo",
        })
        self.assertTrue(form.is_valid())
        with patch("oregoninvasiveshotline.reports.forms.Invite.create", side_effect=lambda *args, **kwargs: kwargs['email'] == "foo@pdx.edu") as m:
            user = self.create_user()
            report = make(Report)
            request = Mock()
            invite_report = form.save(user=user, report=report, request=request)
            self.assertTrue(m.call_count, 2)
            m.assert_any_call(email="foo@pdx.edu", report=report, inviter=user, message="foo", request=request)

            self.assertEqual(invite_report.invited, ["foo@pdx.edu"])
            self.assertEqual(invite_report.already_invited, ["already_invited@pdx.edu"])


class ClaimViewTest(TestCase, UserMixin):

    def setUp(self):
        self.user = self.create_user(
            username="foo@example.com",
            password="foo",
            is_active=True,
            is_staff=False
        )
        self.client.login(email=self.user.email, password="foo")
        self.other_user = self.create_user(
            username="other@example.com",
            password="other",
            is_active=True
        )
        self.index = ReportIndex()
        self.index.clear()

    def tearDown(self):
        self.index.clear()

    def test_claim_unclaimed_report_immediately_claims_it(self):
        report = make(Report, claimed_by=None)
        response = self.client.post(reverse("reports-claim", args=[report.pk]))
        self.assertEqual(Report.objects.get(claimed_by=self.user), report)
        self.assertRedirects(response, reverse("reports-detail", args=[report.pk]))

    def test_already_claimed_report_renders_confirmation_page(self):
        report = make(Report, claimed_by=self.other_user)
        response = self.client.post(reverse("reports-claim", args=[report.pk]))
        self.assertIn("Are you sure you want to steal", response.content.decode())

    def test_stealing_already_claimed_report(self):
        report = make(Report, claimed_by=self.other_user)
        response = self.client.post(reverse("reports-claim", args=[report.pk]), {"steal": 1})
        self.assertEqual(Report.objects.get(claimed_by=self.user), report)
        self.assertRedirects(response, reverse("reports-detail", args=[report.pk]))


class ReportListView(TestCase, UserMixin):

    def setUp(self):
        self.user = self.create_user(
            username="foo@example.com",
            password="foo",
            is_active=True,
            is_staff=False
        )
        self.index = ReportIndex()
        self.index.clear()

    def tearDown(self):
        self.index.clear()

    def test_get(self):
        reports = make(Report, _quantity=3)
        self.client.login(email=self.user.email, password="foo")
        response = self.client.get(reverse("reports-list"))
        self.assertEqual(response.status_code, 200)
        self.assertIn(str(reports[0]), response.content.decode())

    def test_search(self):
        other_reports = make(Report, _quantity=3)
        make(Report, reported_category__name="Foobarius Foobar")

        self.client.login(email=self.user.email, password="foo")
        response = self.client.get(reverse("reports-list") + "?q=foobarius&sort_by=category")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Foobarius Foobar", response.content.decode())
        self.assertNotIn(str(other_reports[0]), response.content.decode())


class UnclaimViewTest(TestCase, UserMixin):

    def setUp(self):
        self.user = self.create_user(
            username="foo@example.com",
            password="foo",
            is_active=True,
            is_staff=False
        )
        self.index = ReportIndex()
        self.index.clear()

    def tearDown(self):
        self.index.clear()

    def test_only_person_who_claimed_report_can_unclaim_it(self):
        report = make(Report)
        # to set it back to False
        self.client.login(email=self.user.email, password="foo")

        response = self.client.get(reverse("reports-unclaim", args=[report.pk]))
        self.assertEqual(response.status_code, 403)

        report.claimed_by = self.user
        report.save()
        response = self.client.get(reverse("reports-unclaim", args=[report.pk]))
        self.assertEqual(response.status_code, 200)

        response = self.client.post(reverse("reports-unclaim", args=[report.pk]))
        report.refresh_from_db()
        self.assertEqual(None, report.claimed_by)


class ExportTest(TestCase):

    def setUp(self):
        self.index = ReportIndex()
        self.index.clear()

    def tearDown(self):
        self.index.clear()

    def test_csv(self):
        reports = make(Report, _quantity=3)
        response = _export(reports, format="csv")
        reader = csv.DictReader(codecs.iterdecode(response, "utf8"))
        rows = list(reader)
        self.assertEqual(3, len(rows))
        self.assertEqual(rows[2]['Description'], reports[2].description)

    def test_kml(self):
        reports = make(Report, _quantity=3)
        response = _export(reports, format="kml")
        # this is harder to test without trying to parse the XML
        self.assertIn(reports[0].description, response.content.decode())


class DeleteViewTest(TestCase, UserMixin):

    def setUp(self):
        self.user = self.create_user(
            username="foo@example.com",
            password="foo",
            is_active=True,
            is_staff=False
        )
        self.index = ReportIndex()
        self.index.clear()

    def tearDown(self):
        self.index.clear()

    def test_permissions(self):
        report = make(Report)
        response = self.client.get(reverse("reports-delete", args=[report.pk]))
        self.assertRedirects(response, reverse("login") + "?next=" + reverse("reports-delete", args=[report.pk]))

        self.client.login(email=self.user.email, password="foo")
        self.user.is_active = False
        self.user.save()
        response = self.client.get(reverse("reports-delete", args=[report.pk]))
        self.assertEqual(response.status_code, 403)

    def test_get(self):
        self.client.login(email=self.user.email, password="foo")
        report = make(Report)
        response = self.client.get(reverse("reports-delete", args=[report.pk]))
        self.assertEqual(response.status_code, 200)

    def test_post(self):
        self.client.login(email=self.user.email, password="foo")
        report = make(Report)
        make(Report)
        response = self.client.post(reverse("reports-delete", args=[report.pk]))
        self.assertRedirects(response, reverse("reports-list"))
        self.assertFalse(Report.objects.filter(pk=report.pk).exists())
        self.assertEqual(Report.objects.count(), 1)